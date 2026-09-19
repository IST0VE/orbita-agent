/**
 * Применяется ответ только последнего запроса.
 *
 * Чтение документа живёт в трёх местах — база знаний, дерево материалов и
 * список публикаций, — и во всех трёх ответ применялся безусловно. Открыли
 * `a.md`, передумали, открыли `b.md`; ответ по `a.md` пришёл вторым и заменил
 * собой уже открытый `b.md`. Порядок сетевых ответов становился порядком
 * навигации, а оператор оказывался в документе, который больше не выбирал.
 *
 * Отмены запроса здесь нет намеренно: `AbortController` прекратил бы загрузку,
 * но правило нужно и без него — устаревший ответ не применяется, даже если
 * успел приехать целиком. Размонтирование считается сменой выбора: сценарий
 * переключили, и ответ прошлого экрана ничей.
 */
import { useCallback, useEffect, useMemo, useRef } from "react";

export type LatestRequest = {
  /** Начать запрос и получить его номер. */
  begin: () => number;
  /** Этот номер всё ещё последний: ответ можно применять. */
  current: (issued: number) => boolean;
};

export function useLatestRequest(): LatestRequest {
  const issued = useRef(0);
  useEffect(() => () => { issued.current += 1; }, []);
  const begin = useCallback(() => (issued.current += 1), []);
  const current = useCallback((token: number) => token === issued.current, []);
  return useMemo(() => ({ begin, current }), [begin, current]);
}
