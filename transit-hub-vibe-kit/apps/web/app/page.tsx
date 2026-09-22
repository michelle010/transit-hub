import { getApiHealth } from "@/lib/api";
import TransferWorkspace from "@/components/transfer/TransferWorkspace";

export default async function HomePage() {
  const health = await getApiHealth();

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand" aria-label="城市中转枢纽">
          <span className="brand-mark" aria-hidden="true">
            枢
          </span>
          <span>城市中转枢纽</span>
        </div>
        <p className="product-label">交通枢纽中转决策工具</p>
      </header>

      <section className="intro-panel" aria-labelledby="product-title">
        <p className="eyebrow">中国大陆城市中转助手</p>
        <h1 id="product-title">到达后，应该去哪个铁路站？</h1>
        <p>
          输入到达枢纽、时间和目的城市，我们会比较同城铁路客运站，结合预计市内交通、建议预留时间和当天列车时刻给出中转建议。
        </p>
      </section>

      <section className="status-panel" aria-labelledby="status-title">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">服务状态</p>
            <h2 id="status-title">服务连接</h2>
          </div>
          <span className={`status-pill ${health.ok ? "status-ok" : "status-error"}`} role="status">
            <span className="status-dot" aria-hidden="true" />
            {health.ok ? "API 可用" : "API 未连接"}
          </span>
        </div>

        <dl className="service-list">
          <div>
            <dt>接口</dt>
            <dd>
              <code>GET /health</code>
            </dd>
          </div>
          {health.ok ? (
            <>
              <div>
                <dt>服务</dt>
                <dd>{health.data.service}</dd>
              </div>
              <div>
                <dt>时区</dt>
                <dd>{health.data.timezone}</dd>
              </div>
            </>
          ) : (
            <div>
              <dt>说明</dt>
              <dd>{health.message}</dd>
            </div>
          )}
        </dl>
      </section>

      <TransferWorkspace />

      <p className="shell-note">
        结果使用计划车次和预计接驳时间，仅作出行规划参考，不代表实际运行或连接保证。
      </p>
    </main>
  );
}
