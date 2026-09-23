import { expect, test, type Page } from "@playwright/test";

import { chengduHubs, discoveryCities, discoveryHubsForCity } from "./fixtures/discovery";
import { transferFixture, type TransferScenario } from "./fixtures/transfer";

const cityById = new Map(discoveryCities.map((city) => [city.id, city]));

function cityDto(city: (typeof discoveryCities)[number]) {
  return {
    id: city.id,
    name_zh: city.name,
    name_en: city.nameEn,
    province_name_zh: city.provinceName,
    adcode: city.adcode,
  };
}

function hubDto(hub: (typeof chengduHubs)[number]) {
  return {
    id: hub.id,
    canonical_name_zh: hub.name,
    canonical_name_en: hub.nameEn,
    hub_type: hub.type,
    importance_level: hub.importanceLevel,
    longitude: null,
    latitude: null,
    coordinate_system: null,
    railway_station_code: null,
    active: hub.active,
    passenger_service: hub.passengerService,
    source: "e2e-fixture",
    aliases: hub.aliases,
  };
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

interface DiscoveryOptions {
  failCityQueries?: Set<string>;
  delayCityQuery?: string;
  delayCityMilliseconds?: number;
}

async function mockDiscovery(page: Page, options: DiscoveryOptions = {}) {
  await page.route("**/api/cities/search**", async (route) => {
    const url = new URL(route.request().url());
    const query = url.searchParams.get("q") ?? "";
    if (options.delayCityQuery === query) {
      await delay(options.delayCityMilliseconds ?? 350);
    }
    if (options.failCityQueries?.has(query)) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          error: { code: "DATABASE_UNAVAILABLE", message: "discovery unavailable" },
        }),
      });
      return;
    }
    const normalized = query.toLowerCase();
    const items = discoveryCities.filter((city) =>
      [city.name, city.nameEn ?? "", city.provinceName].some((value) =>
        value.toLowerCase().includes(normalized),
      ),
    );
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ query, count: items.length, items: items.map(cityDto) }),
    });
  });

  await page.route("**/api/cities/*/hubs", async (route) => {
    const pathParts = new URL(route.request().url()).pathname.split("/");
    const cityId = decodeURIComponent(pathParts[pathParts.length - 2] ?? "");
    const city = cityById.get(cityId);
    const hubs = city ? discoveryHubsForCity(city.id) : [];
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        city: city ? cityDto(city) : cityDto(discoveryCities[0]),
        count: hubs.length,
        items: hubs.map((hub) => hubDto(hub)),
      }),
    });
  });
}

async function mockEvaluation(
  page: Page,
  scenario: TransferScenario,
  options: { delayMilliseconds?: number; status?: number } = {},
) {
  const requests: Record<string, unknown>[] = [];
  await page.route("**/api/transfer/evaluate", async (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    requests.push(body);
    if (options.delayMilliseconds) await delay(options.delayMilliseconds);
    if (options.status && options.status !== 200) {
      const errors: Record<number, { code: string; message: string }> = {
        404: { code: "ARRIVAL_HUB_NOT_FOUND", message: "arrival hub not found" },
        409: { code: "ARRIVAL_HUB_AMBIGUOUS", message: "arrival hub ambiguous" },
        503: { code: "ROUTING_PROVIDER_UNAVAILABLE", message: "routing unavailable" },
      };
      await route.fulfill({
        status: options.status,
        contentType: "application/json",
        body: JSON.stringify({ error: errors[options.status] }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(transferFixture(scenario)),
    });
  });
  return requests;
}

async function selectSuggestion(page: Page, label: string, query: string, option: RegExp) {
  const input = page.getByRole("combobox", { name: label });
  await input.fill(query);
  await expect(page.getByRole("option", { name: option })).toBeVisible();
  await input.press("ArrowDown");
  await input.press("Enter");
}

async function fillCanonicalForm(page: Page) {
  await selectSuggestion(page, "中转城市", "成", /成都/);
  await selectSuggestion(page, "到达枢纽", "成都天府", /成都天府国际机场/);
  await selectSuggestion(page, "目的城市", "乐", /乐山/);
  await page.getByLabel("到达日期").fill("2026-09-18");
  await page.getByLabel("到达时间").fill("14:20");
  await page.getByLabel("托运行李").selectOption("CHECKED");
}

async function fillRailwayArrivalForm(page: Page, arrivalHub: string) {
  await selectSuggestion(page, "中转城市", "成", /成都/);
  await selectSuggestion(page, "到达枢纽", arrivalHub, new RegExp(arrivalHub));
  await selectSuggestion(page, "目的城市", "乐", /乐山/);
  await page.getByLabel("到达日期").fill("2026-09-18");
  await page.getByLabel("到达时间").fill("14:20");
  await page.getByLabel("托运行李").selectOption("CHECKED");
}

async function submit(page: Page) {
  await page.getByRole("button", { name: "开始评估" }).click();
}

async function assertNoHorizontalOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
}

function candidateArticle(page: Page, stationName: string) {
  return page.locator("article.candidate-result").filter({ hasText: stationName }).first();
}

test.describe("canonical transfer flow", () => {
  test("hydrates a shared URL without auto-submitting and preserves China-local time", async ({
    page,
  }) => {
    await mockDiscovery(page);
    const requests = await mockEvaluation(page, "complete");
    const params = new URLSearchParams({
      transfer_city: "成都",
      arrival_hub: "成都天府机场",
      destination_city: "乐山",
      arrival_date: "2026-09-18",
      arrival_time: "14:20",
      baggage: "CHECKED",
      modes: "DRIVING,TRANSIT",
      rail_horizon_hours: "12",
      include_alternative_hubs: "1",
      flexible_dates: "1",
      days_before: "1",
      days_after: "2",
    });
    await page.goto(`/?${params.toString()}`);

    await expect(page.getByRole("combobox", { name: "中转城市" })).toHaveValue("成都");
    await expect(page.getByRole("combobox", { name: "到达枢纽" })).toHaveValue("成都天府机场");
    await expect(page.getByRole("combobox", { name: "目的城市" })).toHaveValue("乐山");
    await expect(page.getByLabel("到达日期")).toHaveValue("2026-09-18");
    await expect(page.getByLabel("到达时间")).toHaveValue("14:20");
    await expect(page.getByLabel("托运行李")).toHaveValue("CHECKED");
    await expect(page.getByRole("checkbox", { name: "显示附近替代交通枢纽" })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: "比较前后日期" })).toBeChecked();
    await expect(page.locator('select[name="flexible_days_before"]')).toHaveValue("1");
    await expect(page.locator('select[name="flexible_days_after"]')).toHaveValue("2");

    await page.waitForTimeout(150);
    expect(requests).toHaveLength(0);
    await submit(page);
    expect(requests[0]).toMatchObject({
      arrival_at: "2026-09-18T14:20:00+08:00",
      include_alternative_hubs: true,
      flexible_dates: { enabled: true, days_before: 1, days_after: 2 },
    });
  });

  test("copies the last submitted query and excludes response state", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async (value: string) => {
            (window as Window & { __copiedShareUrl?: string }).__copiedShareUrl = value;
          },
        },
      });
    });
    await mockDiscovery(page);
    await mockEvaluation(page, "complete");
    await page.goto("/");

    await fillCanonicalForm(page);
    await submit(page);
    await expect(page.getByRole("heading", { name: "成都东站" }).first()).toBeVisible();
    const submittedUrl = new URL(page.url());
    expect(submittedUrl.searchParams.get("transfer_city")).toBe("成都");
    expect(submittedUrl.searchParams.get("arrival_time")).toBe("14:20");
    await page.getByRole("combobox", { name: "目的城市" }).fill("重庆");
    await page.getByRole("button", { name: "复制查询链接" }).click();
    await expect(page.locator(".share-status")).toContainText("查询链接已复制");

    const copiedUrl = await page.evaluate(
      () => (window as Window & { __copiedShareUrl?: string }).__copiedShareUrl,
    );
    expect(copiedUrl).toBeTruthy();
    const copied = new URL(copiedUrl as string);
    expect(copied.searchParams.get("transfer_city")).toBe("成都");
    expect(copied.searchParams.get("destination_city")).toBe("乐山");
    expect(copied.searchParams.get("arrival_at")).toBeNull();
    expect(copied.searchParams.get("provider_summary")).toBeNull();
    expect(copied.searchParams.get("score")).toBeNull();
  });

  test("reports clipboard failure without exposing the browser error", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, "clipboard", {
        configurable: true,
        value: {
          writeText: async () => {
            throw new Error("clipboard secret detail");
          },
        },
      });
    });
    await mockDiscovery(page);
    await mockEvaluation(page, "complete");
    await page.goto("/");

    await fillCanonicalForm(page);
    await submit(page);
    await page.getByRole("button", { name: "复制查询链接" }).click();
    await expect(page.locator(".share-status")).toContainText(
      "无法自动复制，请从浏览器地址栏复制当前链接。",
    );
    await expect(page.locator("body")).not.toContainText("clipboard secret detail");
  });

  test("malformed shared parameters do not blank the page", async ({ page }) => {
    await mockDiscovery(page);
    await page.goto(
      "/?arrival_date=garbage&arrival_time=99%3A90&baggage=ALIEN&modes=INVALID&days_before=500",
    );

    await expect(page.getByRole("heading", { name: "输入你的到达信息" })).toBeVisible();
    await expect(page.getByLabel("到达日期")).toHaveValue("2026-09-18");
    await expect(page.getByLabel("到达时间")).toHaveValue("14:20");
    await expect(page.getByLabel("托运行李")).toHaveValue("UNKNOWN");
    await expect(page.getByRole("checkbox", { name: "公共交通" })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: "驾车" })).toBeChecked();
  });

  test("completes autocomplete, request contract, recommendation and train expansion", async ({
    page,
  }) => {
    await mockDiscovery(page);
    const requests = await mockEvaluation(page, "complete");
    await page.goto("/");

    await fillCanonicalForm(page);
    await submit(page);

    await expect(page.getByRole("button", { name: "正在比较候选车站…" })).toBeDisabled();
    await expect(page.getByRole("heading", { name: "成都东站" }).first()).toBeVisible();
    await expect(page.getByText("备选也值得比较")).toBeVisible();
    await expect(page.getByRole("heading", { name: "成都南站" })).toBeVisible();

    const eastArticle = candidateArticle(page, "成都东站");
    await expect(eastArticle.getByText("首选车次", { exact: true })).toBeVisible();
    await expect(eastArticle.getByText("备选车次", { exact: true })).toBeVisible();
    await expect(eastArticle.getByText("与首选相隔 65 分钟", { exact: true })).toBeVisible();
    const routeToggle = eastArticle.locator("button.route-detail-toggle").first();
    await expect(routeToggle).toHaveAttribute("aria-expanded", "false");
    await routeToggle.click();
    await expect(routeToggle).toHaveAttribute("aria-expanded", "true");
    await expect(eastArticle.getByText("步行前往地铁站", { exact: true })).toBeVisible();
    await expect(eastArticle.getByText("地铁 18 号线", { exact: true }).first()).toBeVisible();
    const drivingToggle = eastArticle.locator("button.route-detail-toggle").nth(1);
    await drivingToggle.click();
    await expect(eastArticle.getByText("沿机场高速前往成都东站", { exact: true })).toBeVisible();

    const body = requests[0];
    expect(body).toMatchObject({
      transfer_city: "成都",
      arrival_hub: "成都天府国际机场",
      destination_city: "乐山",
      arrival_at: "2026-09-18T14:20:00+08:00",
      baggage: "CHECKED",
      allowed_modes: ["TRANSIT", "DRIVING"],
      rail_horizon_hours: 12,
    });
    expect(body).not.toHaveProperty("arrival_datetime");
    expect(body).not.toHaveProperty("baggage_mode");
    expect(body).not.toHaveProperty("route_modes");

    const trainToggle = eastArticle.getByRole("button", {
      name: /查看相关车次/,
    });
    await expect(trainToggle).toHaveAttribute("aria-expanded", "false");
    await trainToggle.click();
    await expect(
      candidateArticle(page, "成都东站").getByRole("button", { name: /收起相关车次/ }),
    ).toHaveAttribute("aria-expanded", "true");
    await expect(eastArticle.getByText("C5771/C5774", { exact: true })).toBeVisible();
    await expect(eastArticle.getByText(/17:26 → 18:26/)).toBeVisible();
    await expect(eastArticle.getByText(/次日 09:10/)).toBeVisible();
    await expect(eastArticle.getByText("余量充足", { exact: true }).first()).toBeVisible();
    await expect(
      eastArticle.locator(".train-presentation-role", { hasText: "首选" }),
    ).toBeVisible();
    await expect(
      eastArticle.locator(".train-presentation-role", { hasText: "备选" }),
    ).toBeVisible();
    await assertNoHorizontalOverflow(page);
  });

  test("shows railway source provenance in a complete result", async ({ page }) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "complete");
    await page.goto("/");
    await fillCanonicalForm(page);
    await submit(page);

    await expect(page.getByText("铁路时刻数据来源：")).toBeVisible();
    await expect(page.getByText(/CHINA_RAILWAY_GTFS/)).toBeVisible();
    await expect(page.getByText(/来源更新时间：2026-09-18 00:00/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "成都东站" }).first()).toBeVisible();
  });

  test("shows stale railway warning without changing recommendation UI", async ({ page }) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "stale_source");
    await page.goto("/");
    await fillCanonicalForm(page);
    await submit(page);

    await expect(
      page.getByText("铁路时刻数据可能已过期，请在出行前再次核对最新时刻。"),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "成都东站" }).first()).toBeVisible();
  });

  test("shows unknown railway timestamp warning and remains usable on mobile", async ({
    page,
  }, testInfo) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "unknown_source");
    await page.goto("/");
    await fillCanonicalForm(page);
    await submit(page);

    await expect(page.getByText("铁路数据更新时间未知，请在出行前核对最新时刻。")).toBeVisible();
    await expect(page.getByRole("heading", { name: "成都东站" }).first()).toBeVisible();
    if (testInfo.project.name === "mobile-chromium") await assertNoHorizontalOverflow(page);
  });

  test("marks a destination-side nearby railway alternative without changing the request", async ({
    page,
  }) => {
    await mockDiscovery(page);
    const requests = await mockEvaluation(page, "nearby");
    await page.goto("/");

    await fillCanonicalForm(page);
    await submit(page);

    await expect(page.getByRole("combobox", { name: "目的城市" })).toHaveValue("乐山");
    const eastArticle = candidateArticle(page, "成都东站");
    await eastArticle.getByRole("button", { name: /查看相关车次/ }).click();
    await expect(eastArticle.getByText("到达：峨眉山站", { exact: true }).first()).toBeVisible();
    await expect(
      eastArticle.getByText("附近替代站 · 距目标约 28.4 公里", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      eastArticle.getByText("尚未计算从该站到最终目的地的接驳时间。", { exact: true }).first(),
    ).toBeVisible();
    expect(requests[0]).toMatchObject({
      destination_city: "乐山",
      include_alternative_hubs: false,
    });
    await assertNoHorizontalOverflow(page);
  });

  test("keeps a nearby arrival airport as a separate what-if comparison", async ({ page }) => {
    await mockDiscovery(page);
    const requests = await mockEvaluation(page, "airport_alternatives");
    await page.goto("/");

    await fillCanonicalForm(page);
    await page.getByRole("checkbox", { name: "显示附近替代交通枢纽" }).check();
    await submit(page);

    await expect(page.getByRole("heading", { name: "成都东站" }).first()).toBeVisible();
    const airportSection = page.getByRole("region", { name: "附近到达机场参考" });
    await expect(airportSection).toBeVisible();
    await expect(airportSection.getByText("成都双流国际机场", { exact: true })).toBeVisible();
    await expect(airportSection.getByText("附近替代机场", { exact: true })).toBeVisible();
    await expect(
      airportSection.getByText("距当前到达机场约 56.0 公里", { exact: true }),
    ).toBeVisible();
    await expect(
      airportSection.getByText("以下方案假设你可以在相同时间抵达该机场，仅供比较后续中转条件。", {
        exact: true,
      }),
    ).toBeVisible();
    await airportSection.locator("summary", { hasText: "查看该机场对应的候选铁路站" }).click();
    await expect(airportSection.getByRole("heading", { name: "成都南站", level: 3 })).toBeVisible();
    expect(requests[0]).toMatchObject({ include_alternative_hubs: true });
    await assertNoHorizontalOverflow(page);
  });

  test("compares bounded China-local arrival dates without changing the primary result", async ({
    page,
  }) => {
    await mockDiscovery(page);
    const requests = await mockEvaluation(page, "flexible_dates");
    await page.goto("/");

    await fillCanonicalForm(page);
    await page.getByRole("checkbox", { name: "比较前后日期" }).check();
    await submit(page);

    const comparison = page.getByRole("region", { name: "铁路衔接便利度" });
    await expect(comparison).toBeVisible();
    await expect(comparison.getByText("当前选择", { exact: true })).toBeVisible();
    await expect(comparison.getByText("9月17日", { exact: true })).toBeVisible();
    await expect(comparison.getByText("9月18日", { exact: true })).toBeVisible();
    await expect(comparison.getByRole("heading", { name: "铁路衔接便利度" })).toBeVisible();
    await expect(comparison.getByText("78", { exact: true })).toBeVisible();
    await expect(
      comparison.getByText(
        "只根据可行车次数、衔接余量和车次时间分布计算，不代表票价、余票、客流或航班情况。",
        {
          exact: true,
        },
      ),
    ).toBeVisible();
    await expect(page.getByRole("heading", { name: "成都东站" }).first()).toBeVisible();
    expect(requests[0]).toMatchObject({
      flexible_dates: { enabled: true, days_before: 1, days_after: 1 },
    });
    await assertNoHorizontalOverflow(page);
  });

  test("keeps an unavailable optional date visible without hiding other dates", async ({
    page,
  }) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "flexible_unavailable");
    await page.goto("/");

    await fillCanonicalForm(page);
    await page.getByRole("checkbox", { name: "比较前后日期" }).check();
    await submit(page);

    const comparison = page.getByRole("region", { name: "铁路衔接便利度" });
    await expect(comparison.getByText("数据不可用", { exact: true })).toBeVisible();
    await expect(
      comparison.getByText("该日期超出当前铁路数据范围。", { exact: true }),
    ).toBeVisible();
    await expect(comparison.getByText("9月18日", { exact: true })).toBeVisible();
    await assertNoHorizontalOverflow(page);
  });

  test("renders partial date comparison as usable data", async ({ page }) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "flexible_partial");
    await page.goto("/");

    await fillCanonicalForm(page);
    await page.getByRole("checkbox", { name: "比较前后日期" }).check();
    await submit(page);

    const comparison = page.getByRole("region", { name: "铁路衔接便利度" });
    await expect(comparison.getByText("部分数据", { exact: true })).toBeVisible();
    await expect(
      comparison.getByText("部分路线或铁路数据暂时不可用，便利度仅基于当前可用数据。", {
        exact: true,
      }),
    ).toBeVisible();
    await assertNoHorizontalOverflow(page);
  });

  test("submits a manually typed alias without selecting a hub suggestion", async ({ page }) => {
    await mockDiscovery(page);
    const requests = await mockEvaluation(page, "complete");
    await page.goto("/");

    await selectSuggestion(page, "中转城市", "成", /成都/);
    const hubInput = page.getByRole("combobox", { name: "到达枢纽" });
    await hubInput.fill("成都天府机场");
    await expect(page.getByRole("option", { name: /成都天府国际机场/ })).toBeVisible();
    await selectSuggestion(page, "目的城市", "乐", /乐山/);
    await submit(page);

    await expect(page.getByRole("heading", { name: "成都东站" }).first()).toBeVisible();
    expect(requests[0]).toMatchObject({ arrival_hub: "成都天府机场" });
  });

  test("keeps free text usable when discovery fails", async ({ page }) => {
    await mockDiscovery(page, { failCityQueries: new Set(["成"]) });
    const requests = await mockEvaluation(page, "complete");
    await page.goto("/");

    const cityInput = page.getByRole("combobox", { name: "中转城市" });
    await cityInput.fill("成");
    await expect(page.getByText("暂时无法加载建议，仍可直接输入名称。")).toBeVisible();
    await cityInput.fill("成都");
    await selectSuggestion(page, "目的城市", "乐", /乐山/);
    await submit(page);

    await expect(page.getByRole("heading", { name: "成都东站" }).first()).toBeVisible();
    expect(requests[0]).toMatchObject({ transfer_city: "成都" });
  });

  test("ignores a stale slower city response", async ({ page }) => {
    await mockDiscovery(page, { delayCityQuery: "成", delayCityMilliseconds: 450 });
    await page.goto("/");

    const cityInput = page.getByRole("combobox", { name: "中转城市" });
    await cityInput.fill("成");
    await expect(cityInput).toHaveAttribute("aria-busy", "true");
    await cityInput.fill("南京");
    await expect(page.getByRole("option", { name: /南京/ })).toBeVisible();
    await expect(page.getByRole("option", { name: /成都/ })).toHaveCount(0);
  });

  test("refreshes hub context when city changes without rewriting typed hub text", async ({
    page,
  }) => {
    await mockDiscovery(page);
    await page.goto("/");

    await selectSuggestion(page, "中转城市", "成", /成都/);
    const hubInput = page.getByRole("combobox", { name: "到达枢纽" });
    await hubInput.fill("成都天府");
    await expect(page.getByRole("option", { name: /成都天府国际机场/ })).toBeVisible();

    const cityInput = page.getByRole("combobox", { name: "中转城市" });
    await cityInput.fill("南京");
    await expect(page.getByRole("option", { name: /成都天府国际机场/ })).toHaveCount(0);
    await expect(hubInput).toHaveValue("成都天府");
  });

  test("renders PARTIAL results without turning them into a full error", async ({ page }) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "partial");
    await page.goto("/");
    await submit(page);

    await expect(page.getByRole("heading", { name: "成都东站" }).first()).toBeVisible();
    await expect(page.getByText(/部分路线数据暂时不可用/)).toBeVisible();
    await expect(page.getByText(/驾车 \/ 出租车路线暂时无法获取/).first()).toBeVisible();
    const eastArticle = candidateArticle(page, "成都东站");
    const routeToggle = eastArticle.locator("button.route-detail-toggle").first();
    await routeToggle.click();
    await expect(eastArticle.getByText("步行前往地铁站", { exact: true })).toBeVisible();
    await expect(page.locator(".api-error")).toHaveCount(0);
  });

  test("distinguishes a current-period transit no-route from a provider failure", async ({
    page,
  }) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "transit_unavailable");
    await page.goto("/");
    await submit(page);

    await expect(page.getByText("公共交通路线在当前查询时段不可用").first()).toBeVisible();
    await expect(page.getByText(/驾车 \/ 出租车/).first()).toBeVisible();
    await expect(page.locator(".api-error")).toHaveCount(0);
  });

  test("shows provider failure wording without implying public transit is out of service", async ({
    page,
  }) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "transit_provider_failure");
    await page.goto("/");
    await submit(page);

    await expect(page.getByText("公共交通路线暂时无法查询").first()).toBeVisible();
    await expect(page.getByText("公共交通路线在当前查询时段不可用")).toHaveCount(0);
    await expect(page.locator(".api-error")).toHaveCount(0);
  });

  test("does not silently add driving when transit-only has no route", async ({ page }) => {
    await mockDiscovery(page);
    const requests = await mockEvaluation(page, "transit_only_unavailable");
    await page.goto("/");
    await fillCanonicalForm(page);
    await page.getByRole("checkbox", { name: "驾车 / 出租车" }).uncheck();
    await submit(page);

    await expect(page.getByText("公共交通路线在当前查询时段不可用").first()).toBeVisible();
    await expect(page.getByText("驾车 / 出租车路线")).toHaveCount(0);
    await expect(page.locator(".api-error")).toHaveCount(0);
    expect(requests[0]?.allowed_modes).toEqual(["TRANSIT"]);
  });

  test("renders risky no-recommendation semantics", async ({ page }) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "risky");
    await page.goto("/");
    await submit(page);

    await expect(
      page.getByRole("heading", { name: "当前条件下没有找到足够稳妥的中转方案。" }),
    ).toBeVisible();
    await expect(
      page.getByText("当前条件下没有足够稳妥的中转方案。", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("成都东站").first()).toBeVisible();
  });

  test("renders an all-infeasible result as a business outcome", async ({ page }) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "infeasible");
    await page.goto("/");
    await submit(page);

    await expect(
      page.getByRole("heading", { name: "当前条件下没有找到足够稳妥的中转方案。" }),
    ).toBeVisible();
    await expect(page.getByText(/当前查询时段内没有可行的计划车次/)).toBeVisible();
    await expect(page.getByText("成都西站").first()).toBeVisible();
  });

  test("explains a railway arrival and same-station transfer", async ({ page }) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "railway_same_station");
    await page.goto("/");
    await fillRailwayArrivalForm(page, "成都东");
    await submit(page);

    const east = candidateArticle(page, "成都东站");
    await expect(east).toContainText("同站换乘");
    await expect(east).toContainText("不需要前往另一座铁路站");
    await expect(east).not.toContainText("暂无可用路线");
  });

  test("explains a railway arrival and cross-station transfer", async ({ page }) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "railway_cross_station");
    await page.goto("/");
    await fillRailwayArrivalForm(page, "成都东");
    await submit(page);

    const east = candidateArticle(page, "成都东站");
    await expect(east).toContainText("需要跨站换乘");
    await expect(east).toContainText("成都站前往成都东站");
    await expect(east).toContainText("公共交通");
  });

  for (const [status, code, message] of [
    [404, "ARRIVAL_HUB_NOT_FOUND", "没有找到这个到达枢纽"],
    [409, "ARRIVAL_HUB_AMBIGUOUS", "这个枢纽名称对应多个地点"],
    [503, "ROUTING_PROVIDER_UNAVAILABLE", "市内交通路线暂时无法查询"],
  ] as const) {
    test(`keeps form state and presents a typed ${status} error`, async ({ page }) => {
      await mockDiscovery(page);
      const requests = await mockEvaluation(page, "complete", { status });
      await page.goto("/");
      await submit(page);

      await expect(page.locator(".api-error")).toContainText(message);
      await expect(page.getByRole("combobox", { name: "中转城市" })).toHaveValue("成都");
      await expect(page.getByRole("combobox", { name: "到达枢纽" })).toHaveValue("成都天府机场");
      expect(requests[0]).toBeTruthy();
      expect(code).toBeTruthy();
      await expect(page.getByText(code)).toHaveCount(0);
    });
  }

  test("shows loading state while evaluation is pending", async ({ page }) => {
    await mockDiscovery(page);
    await mockEvaluation(page, "complete", { delayMilliseconds: 350 });
    await page.goto("/");
    await submit(page);

    await expect(page.getByText("正在比较候选车站、交通时间和列车时刻…")).toBeVisible();
    await expect(page.getByRole("button", { name: "正在比较候选车站…" })).toBeDisabled();
    await expect(page.getByRole("heading", { name: "成都东站" }).first()).toBeVisible();
  });

  test("supports keyboard selection and Escape without discarding typed text", async ({
    page,
  }, testInfo) => {
    test.skip(
      testInfo.project.name === "mobile-chromium",
      "Keyboard-specific coverage runs on desktop projects",
    );
    await mockDiscovery(page);
    await page.goto("/");

    const cityInput = page.getByRole("combobox", { name: "中转城市" });
    await cityInput.fill("成");
    await expect(page.getByRole("option", { name: /成都/ })).toBeVisible();
    await cityInput.press("Escape");
    await expect(page.getByRole("option", { name: /成都/ })).toHaveCount(0);
    await expect(cityInput).toHaveValue("成");
    await cityInput.press("ArrowDown");
    await cityInput.press("Enter");
    await expect(cityInput).toHaveValue("成都");
  });

  test("works in a narrow mobile viewport without page overflow", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chromium", "Mobile viewport coverage");
    await mockDiscovery(page);
    await mockEvaluation(page, "complete");
    await page.goto("/");

    const cityInput = page.getByRole("combobox", { name: "中转城市" });
    await cityInput.fill("成");
    await expect(page.getByRole("option", { name: /成都/ })).toBeVisible();
    await assertNoHorizontalOverflow(page);
    await page.getByRole("option", { name: /成都/ }).click();
    await selectSuggestion(page, "到达枢纽", "成都天府", /成都天府国际机场/);
    await selectSuggestion(page, "目的城市", "乐", /乐山/);
    await page.getByLabel("到达日期").fill("2026-09-18");
    await page.getByLabel("到达时间").fill("14:20");
    await page.getByLabel("托运行李").selectOption("CHECKED");
    await submit(page);
    await expect(page.getByRole("heading", { name: "成都东站" }).first()).toBeVisible();
    const routeToggle = candidateArticle(page, "成都东站")
      .locator("button.route-detail-toggle")
      .first();
    await routeToggle.click();
    await expect(routeToggle).toHaveAttribute("aria-expanded", "true");
    const trainToggle = candidateArticle(page, "成都东站").getByRole("button", {
      name: /查看相关车次/,
    });
    await trainToggle.click();
    await assertNoHorizontalOverflow(page);
  });
});
