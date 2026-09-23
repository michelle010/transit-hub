import { afterEach, describe, expect, it, vi } from "vitest";

import { DiscoveryApiError, getCityHubs, searchCities } from "./discovery";

const cityPayload = {
  query: "成",
  count: 1,
  items: [
    {
      id: "city-chengdu",
      name_zh: "成都",
      name_en: "Chengdu",
      province_name_zh: "四川省",
      adcode: "510100",
    },
  ],
};

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("canonical discovery API", () => {
  it("maps the city DTO and encodes the query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(cityPayload));
    vi.stubGlobal("fetch", fetchMock);

    await expect(searchCities("成 都")).resolves.toEqual([
      {
        id: "city-chengdu",
        name: "成都",
        nameEn: "Chengdu",
        provinceName: "四川省",
        adcode: "510100",
      },
    ]);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/cities/search?q=%E6%88%90%20%E9%83%BD",
      expect.objectContaining({ signal: undefined }),
    );
  });

  it("keeps only active passenger airport and railway hubs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({
          city: cityPayload.items[0],
          count: 6,
          items: [
            {
              id: "airport",
              canonical_name_zh: "成都天府国际机场",
              canonical_name_en: null,
              hub_type: "AIRPORT",
              importance_level: 5,
              longitude: 104.44,
              latitude: 30.31,
              coordinate_system: "GCJ02",
              railway_station_code: null,
              active: true,
              passenger_service: true,
              source: "seed",
              aliases: ["成都天府机场"],
            },
            {
              id: "railway",
              canonical_name_zh: "成都东站",
              canonical_name_en: "Chengdu East",
              hub_type: "RAILWAY",
              importance_level: 5,
              longitude: 104.14,
              latitude: 30.63,
              coordinate_system: "GCJ02",
              railway_station_code: "ICW",
              active: true,
              passenger_service: true,
              source: "seed",
              aliases: ["成都东"],
            },
            {
              id: "inactive",
              canonical_name_zh: "旧机场",
              canonical_name_en: null,
              hub_type: "AIRPORT",
              importance_level: 1,
              longitude: null,
              latitude: null,
              coordinate_system: null,
              railway_station_code: null,
              active: false,
              passenger_service: true,
              source: "seed",
              aliases: [],
            },
            {
              id: "freight",
              canonical_name_zh: "货运站",
              canonical_name_en: null,
              hub_type: "RAILWAY",
              importance_level: 1,
              longitude: null,
              latitude: null,
              coordinate_system: null,
              railway_station_code: null,
              active: true,
              passenger_service: false,
              source: "seed",
              aliases: [],
            },
            {
              id: "private-airport",
              canonical_name_zh: "非客运机场",
              canonical_name_en: null,
              hub_type: "AIRPORT",
              importance_level: 1,
              longitude: null,
              latitude: null,
              coordinate_system: null,
              railway_station_code: null,
              active: true,
              passenger_service: false,
              source: "seed",
              aliases: [],
            },
            {
              id: "metro",
              canonical_name_zh: "成都地铁站",
              canonical_name_en: null,
              hub_type: "METRO",
              importance_level: 1,
              longitude: null,
              latitude: null,
              coordinate_system: null,
              railway_station_code: null,
              active: true,
              passenger_service: true,
              source: "seed",
              aliases: [],
            },
            {
              id: "unknown",
              canonical_name_zh: "未分类枢纽",
              canonical_name_en: null,
              hub_type: "PORT",
              importance_level: 1,
              longitude: null,
              latitude: null,
              coordinate_system: null,
              railway_station_code: null,
              active: true,
              passenger_service: true,
              source: "seed",
              aliases: [],
            },
          ],
        }),
      ),
    );

    await expect(getCityHubs("city-chengdu")).resolves.toEqual([
      {
        id: "airport",
        name: "成都天府国际机场",
        nameEn: null,
        type: "AIRPORT",
        importanceLevel: 5,
        active: true,
        passengerService: true,
        aliases: ["成都天府机场"],
      },
      {
        id: "railway",
        name: "成都东站",
        nameEn: "Chengdu East",
        type: "RAILWAY",
        importanceLevel: 5,
        active: true,
        passengerService: true,
        aliases: ["成都东"],
      },
    ]);
  });

  it("normalizes discovery HTTP errors without exposing raw payloads", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          jsonResponse(
            { error: { code: "DATABASE_UNAVAILABLE", message: "database details" } },
            503,
          ),
        ),
    );

    await expect(searchCities("成")).rejects.toMatchObject<Partial<DiscoveryApiError>>({
      name: "DiscoveryApiError",
      code: "DATABASE_UNAVAILABLE",
      status: 503,
    });
  });

  it("passes AbortSignal through to the canonical endpoint", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(cityPayload));
    vi.stubGlobal("fetch", fetchMock);

    await searchCities("成", controller.signal);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/cities/search?q=%E6%88%90",
      expect.objectContaining({ signal: controller.signal }),
    );
  });
});
