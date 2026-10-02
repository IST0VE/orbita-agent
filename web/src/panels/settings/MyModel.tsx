/**
 * «Моя модель»: каким подключением и какой моделью ORBITA отвечает мне.
 *
 * Подключения заводит администратор — адрес и ключ лежат на сервере, и сюда
 * приезжают только их названия и списки моделей. Свой адрес вписать нельзя:
 * с ним уехал бы ключ сервера. Выбор действует со следующего запроса к модели,
 * перезапускать ничего не нужно, — и у каждого пользователя он свой.
 *
 * Список моделей объявляет администратор или отдаёт сам шлюз. Шлюз списка не
 * дал — имя модели можно вписать руками, а «Проверить» скажет, отвечает ли она.
 */

import { useEffect, useMemo, useState } from "react";

import { checkModel, loadModel, saveModel, type ModelCheck, type ModelDoc, type ModelPick } from "../../api";
import { StatusDot } from "../../ui";
import { RefreshCw, TriangleAlert } from "../../ui/icons";

const SOURCE: Record<string, string> = {
  list: "список задал администратор",
  gateway: "список отдал шлюз",
  default: "шлюз списка не дал — впишите имя модели",
};

export function MyModel() {
  const [doc, setDoc] = useState<ModelDoc | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"load" | "save" | "check" | null>("load");
  const [pick, setPick] = useState<ModelPick | null>(null);
  const [check, setCheck] = useState<(ModelCheck & { pick: ModelPick }) | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const load = (refresh = false) => {
    setBusy("load");
    setError(null);
    loadModel(refresh)
      .then((value) => {
        setDoc(value);
        setPick((current) => current ?? value.current);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(null));
  };

  useEffect(() => { load(); }, []);

  const endpoint = useMemo(
    () => doc?.endpoints.find((item) => item.id === pick?.endpoint) ?? null,
    [doc, pick?.endpoint],
  );

  if (!doc) {
    return error ? (
      <p className="settings-restart" role="alert"><TriangleAlert size={15} aria-hidden="true" />{error}</p>
    ) : (
      <div className="engine-widget">
        <span className="skeleton" style={{ width: "40%" }} />
        <span className="skeleton" style={{ width: "65%" }} />
      </div>
    );
  }

  const title = (id: string) => doc.endpoints.find((item) => item.id === id)?.title ?? id;
  const currentLine = `${title(doc.current.endpoint)} · ${doc.current.model}`;
  const locked = Boolean(doc.unavailable) || busy !== null;
  const dirty = Boolean(pick) && (pick!.endpoint !== doc.current.endpoint || pick!.model !== doc.current.model);
  const ready = Boolean(pick?.endpoint && pick.model.trim());

  const choose = (endpointId: string) => {
    const next = doc.endpoints.find((item) => item.id === endpointId);
    if (!next) return;
    // Модель другого подключения не подходит этому: берём его модель по
    // умолчанию или первую из списка — и показываем это, а не прячем.
    const own = doc.current.endpoint === endpointId ? doc.current.model : "";
    setPick({ endpoint: endpointId, model: own || next.default_model || next.models[0] || "" });
    setCheck(null);
    setStatus(null);
  };

  const run = async (kind: "save" | "check", action: () => Promise<void>) => {
    setBusy(kind);
    setError(null);
    setStatus(null);
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const save = () => run("save", async () => {
    if (!pick) return;
    const value = await saveModel({ endpoint: pick.endpoint, model: pick.model.trim() });
    setDoc(value);
    setPick(value.current);
    setStatus("Сохранено. Действует со следующего запроса — перезапускать ничего не нужно.");
  });

  const reset = () => run("save", async () => {
    const value = await saveModel({ endpoint: null });
    setDoc(value);
    setPick(value.current);
    setCheck(null);
    setStatus("Вернулась модель сервера по умолчанию.");
  });

  const probe = () => run("check", async () => {
    if (!pick) return;
    const value = { endpoint: pick.endpoint, model: pick.model.trim() };
    setCheck({ ...(await checkModel(value)).check, pick: value });
  });

  // Проверка относится к той паре, которую проверяли: выбрали другую — ответа нет.
  const checked = check && pick && check.pick.endpoint === pick.endpoint && check.pick.model === pick.model.trim()
    ? check : null;

  return (
    <>
      {doc.unavailable ? (
        <p className="settings-restart" role="status">
          <TriangleAlert size={15} aria-hidden="true" />
          {doc.unavailable}
        </p>
      ) : null}
      {doc.problems.map((problem) => (
        <p className="settings-restart" role="alert" key={problem}>
          <TriangleAlert size={15} aria-hidden="true" />
          {problem}
        </p>
      ))}
      {error ? <p className="settings-restart" role="alert"><TriangleAlert size={15} aria-hidden="true" />{error}</p> : null}

      <div className="set-section">
        <h3>Сейчас</h3>
        <div className="set-row">
          <div className="set-field">
            <span className="set-label"><span className="set-name">ORBITA отвечает вам</span></span>
            <span className="set-control set-connection">
              <span className="mono">{currentLine}</span>
            </span>
          </div>
          <div className="set-desc">
            {doc.choice
              ? "Модель выбрана вами. Другие пользователи работают каждый на своей."
              : "Это модель сервера по умолчанию: вы себе модель не выбирали."}
          </div>
        </div>
      </div>

      <div className="set-section">
        <h3>Выбрать</h3>
        {doc.endpoints.length > 1 ? (
          <div className="set-row">
            <div className="set-field">
              <span className="set-label"><span className="set-name">Подключение</span></span>
              <span className="set-control">
                <select
                  value={pick?.endpoint ?? ""}
                  disabled={locked}
                  aria-label="Подключение к модели"
                  onChange={(event) => choose(event.target.value)}
                >
                  {doc.endpoints.map((item) => (
                    <option key={item.id} value={item.id}>{item.title}</option>
                  ))}
                </select>
              </span>
            </div>
            <div className="set-desc">
              Подключения заводит администратор: адрес и ключ остаются на сервере.
            </div>
          </div>
        ) : null}

        {endpoint ? (
          <div className="set-row">
            <div className="set-field">
              <span className="set-label"><span className="set-name">Модель</span></span>
              <span className="set-control">
                {endpoint.source === "default" ? (
                  <input
                    type="text"
                    value={pick?.model ?? ""}
                    disabled={locked}
                    aria-label="Имя модели"
                    placeholder={endpoint.default_model || "имя модели"}
                    onChange={(event) => { setPick({ endpoint: endpoint.id, model: event.target.value }); setStatus(null); }}
                  />
                ) : (
                  <select
                    value={pick?.model ?? ""}
                    disabled={locked}
                    aria-label="Модель"
                    onChange={(event) => { setPick({ endpoint: endpoint.id, model: event.target.value }); setStatus(null); }}
                  >
                    {pick?.model && !endpoint.models.includes(pick.model) ? (
                      <option value={pick.model}>{pick.model} — нет в списке</option>
                    ) : null}
                    {endpoint.models.map((name) => (
                      <option key={name} value={name}>
                        {name}{name === doc.default.model && endpoint.id === doc.default.endpoint ? " — по умолчанию" : ""}
                      </option>
                    ))}
                  </select>
                )}
                <button
                  className="btn-ghost btn-icon btn-sm"
                  disabled={locked}
                  title="Спросить список моделей у шлюза заново"
                  aria-label="Обновить список моделей"
                  onClick={() => load(true)}
                >
                  <RefreshCw size={14} aria-hidden="true" />
                </button>
              </span>
            </div>
            <div className="set-desc">
              {SOURCE[endpoint.source]}
              {endpoint.source === "default" && endpoint.default_model ? `; по умолчанию — ${endpoint.default_model}` : ""}
              {endpoint.error ? ` (${endpoint.error})` : ""}
            </div>
          </div>
        ) : null}

        <div className="set-row">
          <div className="set-field">
            <span className="set-label"><span className="set-name">Проверка</span></span>
            <span className="set-control set-connection">
              {busy === "check" ? (
                <span className="hint">Спрашиваем модель…</span>
              ) : checked ? (
                <>
                  <StatusDot tone={checked.ok ? "ok" : "bad"} />
                  <span>{checked.detail}</span>
                </>
              ) : (
                <span className="hint">не проверялась: один короткий запрос «ответь ок»</span>
              )}
            </span>
          </div>
          <div className="set-actions">
            <button className="btn-primary btn-sm" disabled={!dirty || !ready || locked} onClick={() => void save()}>
              {busy === "save" ? "Сохраняем…" : "Сохранить"}
            </button>
            <button className="btn-sm" disabled={!ready || locked} onClick={() => void probe()}>
              Проверить
            </button>
            <button
              className="btn-ghost btn-sm"
              disabled={!doc.choice || locked}
              title="Работать на модели сервера по умолчанию"
              onClick={() => void reset()}
            >
              Модель сервера
            </button>
          </div>
          {status ? <div className="set-desc ok" role="status">{status}</div> : null}
        </div>
      </div>
    </>
  );
}
