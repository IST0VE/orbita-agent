/**
 * Состояние открытого треда для `useStream` — снаружи, а не внутри SDK.
 *
 * SDK умеет читать состояние треда сам, но перечитать его по просьбе не даёт:
 * это делается только сменой треда. А переключение версии запроса меняет
 * голову того же треда (сервер копирует голову выбранной ветки в конец), и
 * показать её нужно, не уходя из чата. Поэтому состояние читается здесь, тем
 * же запросом, что и в SDK (`threads.getState`), а `mutate` перечитывает его.
 *
 * Пока прогон заводит новый тред, читать его рано: в нём ещё ничего нет, а
 * пустой снимок лёг бы поверх того, что приходит потоком. SDK пропускает это
 * чтение тем же образом (`submittingRef`).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Client, ThreadState } from "@langchain/langgraph-sdk";

type Snapshot<T> = {
  key: string | null;
  data: ThreadState<T>[] | null | undefined;
  error: unknown;
  isLoading: boolean;
};

export type ThreadStateSource<T extends Record<string, unknown>> = {
  data: ThreadState<T>[] | null | undefined;
  error: unknown;
  isLoading: boolean;
  mutate: (threadId?: string) => Promise<ThreadState<T>[] | null | undefined>;
};

export function useThreadState<T extends Record<string, unknown>>(
  client: Client,
  threadId: string | null,
  /** Идёт ли прогон: заведённый им тред читать рано. */
  streaming: () => boolean,
): ThreadStateSource<T> {
  const [state, setState] = useState<Snapshot<T>>({ key: null, data: undefined, error: undefined, isLoading: false });
  const latest = useRef(threadId);
  latest.current = threadId;

  const fetchState = useCallback(async (id: string | null) => {
    if (!id) {
      setState({ key: null, data: undefined, error: undefined, isLoading: false });
      return undefined;
    }
    setState((current) => (current.key === id ? { ...current, isLoading: true } : { key: id, data: undefined, error: undefined, isLoading: true }));
    try {
      const head = await client.threads.getState<T>(id);
      const data = head.checkpoint == null ? [] : [head];
      // Ответ про тред, который уже закрыли, на экран не кладётся.
      if (latest.current === id) setState({ key: id, data, error: undefined, isLoading: false });
      return data;
    } catch (error) {
      if (latest.current === id) setState((current) => ({ ...current, error, isLoading: false }));
      throw error;
    }
  }, [client]);

  useEffect(() => {
    if (threadId && streaming()) {
      setState({ key: threadId, data: undefined, error: undefined, isLoading: false });
      return;
    }
    fetchState(threadId).catch(() => undefined);
    // `streaming` читается в момент смены треда, а не следит за прогоном.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, fetchState]);

  const mutate = useCallback((id?: string) => fetchState(id ?? latest.current), [fetchState]);

  return useMemo(() => ({
    data: state.key === threadId ? state.data : undefined,
    error: state.key === threadId ? state.error : undefined,
    isLoading: state.key === threadId ? state.isLoading : Boolean(threadId),
    mutate,
  }), [state, threadId, mutate]);
}
