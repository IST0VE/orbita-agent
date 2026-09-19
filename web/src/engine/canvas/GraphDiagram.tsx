/** Interactive graph. React Flow owns gestures; ELK owns the initial geometry. */
import { memo, useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from "react";
import {
  Background, BaseEdge, Handle, MarkerType, MiniMap, Panel, Position, ReactFlow,
  ReactFlowProvider, useNodesInitialized, useNodesState, useReactFlow,
  getViewportForBounds, getSmoothStepPath,
  type Edge, type EdgeProps, type Node, type NodeProps,
} from "@xyflow/react";
import { END, START, edgeKey, isTerminal, traversedEdges, type GraphTopology } from "../../lib/graph";
import { Flag, Play, nodeIcon, nodeTone } from "../../ui/icons";
import type { NodeManifest, UiManifest } from "../manifest/types";
import { executionPath, nodeStatus, visitCount } from "../runtime/selectors";
import type { RuntimeSnapshot } from "../runtime/types";
import type { GraphLayout, LayoutNode, NodeBox, Point } from "./layout";
import { layeredLayout } from "./layout";
import ELK from "elkjs/lib/elk-api.js";
import elkWorkerUrl from "elkjs/lib/elk-worker.min.js?url";
import { pathOf, routeEdge } from "./routing";
import { useEdgeRoutes } from "./useEdgeRoutes";
import type { ZoomHandle } from "./ZoomPane";

export function diagramStatus(runtime: RuntimeSnapshot, nodeId: string): string {
  if (nodeId === START) return runtime.runStatus === "idle" ? "idle" : "completed";
  if (nodeId === END) return runtime.runStatus === "completed" ? "completed" : "idle";
  return nodeStatus(runtime, nodeId);
}

/**
 * Масштабы схемы.
 *
 * `READABLE_ZOOM` — граница, ниже которой подписи узлов перестают читаться, и
 * вписывать в неё граф целиком бессмысленно. `ENTRANCE_ZOOM` — масштаб, с
 * которого схему показывают вместо обзора: узел читается целиком.
 */
const MIN_ZOOM = 0.1;
const READABLE_ZOOM = 0.7;
const ENTRANCE_ZOOM = 0.9;

/**
 * Смысловой масштаб: сколько узел рассказывает о себе на этом отдалении.
 *
 * `far` — значок, название и состояние; `mid` — название и строка описания;
 * `near` — всё. Полоса пишется атрибутом прямо в DOM, а не состоянием React:
 * она меняется на каждом кадре перетаскивания полотна, и перерисовывать
 * ради неё два десятка карточек нельзя.
 */
function band(element: HTMLElement | null, zoom: number): void {
  if (element) element.dataset.zoom = zoom < 0.62 ? "far" : zoom < 0.88 ? "mid" : "near";
}

type CardData = {
  layout: LayoutNode; info?: NodeManifest; status: string; count: number; dimmed: boolean;
};
type CardNode = Node<CardData, "card">;
type RoutedEdge = Edge<{ points: Point[]; boxes: NodeBox[]; deferred: boolean; from?: Point; to?: Point }, "routed">;

const GraphCard = memo(function GraphCard({ id, data, selected }: NodeProps<CardNode>) {
  const terminal = isTerminal(id), info = data.info;
  const title = terminal ? id === START ? "Старт" : "Конец" : info?.title ?? id;
  const Icon = terminal ? (id === START ? Play : Flag) : nodeIcon(id, info?.kind);
  return <div
    className={[
      "graph-node", `node-${data.status}`, `kind-${info?.kind ?? "task"}`,
      terminal ? `node-terminal terminal-${id === START ? "start" : "end"}` : nodeTone(info?.kind, info?.color),
      selected ? "selected" : "", data.dimmed ? "dimmed" : "",
    ].filter(Boolean).join(" ")}
    title={info?.description ? `${title} — ${info.description}` : title}
    style={{ width: data.layout.width, height: data.layout.height }}
  >
    {data.layout.ports.map((port) => <Handle key={port.id} id={port.id} type={port.type}
      position={port.type === "source" ? Position.Right : Position.Left}
      style={{ top: port.y }} isConnectable={false} />)}
    {terminal ? <Icon size={15} aria-hidden="true" />
      : <span className="graph-node-icon"><Icon size={16} aria-hidden="true" /></span>}
    <span className="graph-node-text">
      <span className="graph-node-title">{title}</span>
      {!terminal && info?.description ? <span className="graph-node-desc">{info.description}</span> : null}
    </span>
    {!terminal && data.count > 1 ? <span className="graph-node-count">×{data.count}</span> : null}
  </div>;
});

/**
 * Связь между карточками.
 *
 * Концы связи приходят из раскладки, а не из ручек React Flow: их он меряет по
 * DOM и отдаёт край элемента с точностью до субпикселя. От такого конца не
 * совпадал ни один маршрут ELK со своими же портами, и схема перекладывала все
 * связи заново на первом же кадре — а конец, соскользнувший внутрь карточки,
 * превращал её саму в препятствие и уводил связь в обход. Позиция карточки и
 * смещение порта известны точно; ровно их считает и воркер маршрутов.
 */
const GraphLink = memo(function GraphLink(props: EdgeProps<RoutedEdge>) {
  const fromX = props.data?.from?.x ?? props.sourceX, fromY = props.data?.from?.y ?? props.sourceY;
  const toX = props.data?.to?.x ?? props.targetX, toY = props.data?.to?.y ?? props.targetY;
  const points = useMemo(() => props.data?.deferred ? props.data.points : routeEdge(
    { x: fromX, y: fromY }, { x: toX, y: toY },
    props.data?.points ?? [], props.data?.boxes ?? [],
  ), [fromX, fromY, toX, toY, props.data?.points, props.data?.boxes, props.data?.deferred]);
  const first = points[0], last = points[points.length - 1];
  const attached = first && last && Math.abs(first.x - fromX) < 0.1 && Math.abs(first.y - fromY) < 0.1
    && Math.abs(last.x - toX) < 0.1 && Math.abs(last.y - toY) < 0.1;
  const temporary = props.data?.deferred && !attached
    ? getSmoothStepPath({ sourceX: fromX, sourceY: fromY, targetX: toX, targetY: toY,
      sourcePosition: Position.Right, targetPosition: Position.Left, borderRadius: 0 }) : null;
  const middle = points[Math.floor(points.length / 2)];
  return <BaseEdge id={props.id} path={temporary?.[0] ?? pathOf(points)} markerEnd={props.markerEnd}
    style={props.style} label={props.label} labelX={temporary?.[1] ?? middle?.x} labelY={temporary?.[2] ?? middle?.y}
    labelStyle={{ fill: "var(--text-secondary)", fontSize: "var(--type-label)" }}
    labelBgStyle={{ fill: "var(--surface)" }} interactionWidth={16} />;
});
const nodeTypes = { card: GraphCard };
const edgeTypes = { routed: GraphLink };

export type DiagramHandle = ZoomHandle & { arrange: () => void };
type Props = {
  topology: GraphTopology; manifest: UiManifest; runtime: RuntimeSnapshot;
  selected: string | null; onSelect: (id: string | null) => void;
  query: string; minimap: boolean; onZoom: (value: number) => void;
  ref?: Ref<DiagramHandle>;
};

function DiagramScene({ topology, manifest, runtime, selected, onSelect, query, minimap, onZoom, ref }: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState<CardNode>([]);
  const [layout, setLayout] = useState<GraphLayout | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const initialized = useNodesInitialized();
  const flow = useReactFlow<CardNode, RoutedEdge>();
  const fitPending = useRef(false);
  const container = useRef<HTMLDivElement>(null);
  const hints = Object.fromEntries(Object.entries(manifest.nodes ?? {}).map(([id, value]) => [id, value.layout ?? {}]));
  // Runtime snapshots and new object identities must not reset manual placement.
  const layoutKey = JSON.stringify([topology, hints]);
  useEffect(() => {
    setError(""); setLayout(null); setNodes([]);
    let worker: Worker | undefined;
    let cancelled = false;
    let timeout: number | undefined;
    const fail = (reason?: string) => {
      if (cancelled) return;
      cancelled = true;
      worker?.terminate(); window.clearTimeout(timeout);
      setError(reason || "Превышено время ожидания раскладки");
    };
    try {
      worker = new Worker(elkWorkerUrl);
      const elk = new ELK({ workerFactory: () => worker! });
      timeout = window.setTimeout(fail, 30000);
      worker.onerror = (event) => fail(event.message);
      const [graph, layoutHints] = JSON.parse(layoutKey);
      void layeredLayout(graph, layoutHints, elk).then((value) => {
        if (cancelled) return;
        window.clearTimeout(timeout); worker?.terminate();
        fitPending.current = true;
        setLayout(value);
        setNodes(value.nodes.map((node) => ({
          id: node.id, type: "card", position: { x: node.x, y: node.y },
          width: node.width, height: node.height,
          data: { layout: node, status: "idle", count: 0, dimmed: false },
        })));
      }).catch((cause) => { if (!cancelled) fail(cause instanceof Error ? cause.message : String(cause)); });
    } catch (cause) { fail(cause instanceof Error ? cause.message : String(cause)); }
    return () => { cancelled = true; window.clearTimeout(timeout); worker?.terminate(); };
  }, [layoutKey, revision, setNodes]);

  useEffect(() => {
    if (!initialized || !fitPending.current) return;
    const element = container.current;
    if (!element) return;
    const place = () => {
      if (!fitPending.current || !element.clientWidth || !element.clientHeight) return;
      fitPending.current = false;
      const camera = getViewportForBounds(flow.getNodesBounds(nodes), element.clientWidth, element.clientHeight, MIN_ZOOM, 1, 0.16);
      // Весь граф, вписанный в узкую панель, — это 40% масштаба и нечитаемые
      // подписи. Ниже порога читаемости схема не вписывается, а открывается
      // у входа в конвейер: обзор целиком запрашивают кнопкой отдельно.
      if (camera.zoom < READABLE_ZOOM) {
        const entrance = nodes.find((node) => node.id === START) ?? nodes[0];
        if (entrance) void flow.setViewport({ x: 48 - entrance.position.x * ENTRANCE_ZOOM,
          y: Math.max(60, element.clientHeight * 0.34) - entrance.position.y * ENTRANCE_ZOOM, zoom: ENTRANCE_ZOOM });
        band(element, ENTRANCE_ZOOM);
      } else {
        void flow.setViewport(camera);
        band(element, camera.zoom);
      }
    };
    place();
    if (!fitPending.current) return;
    const observer = new ResizeObserver(place);
    observer.observe(element);
    return () => observer.disconnect();
  }, [initialized, layout, flow, nodes]);

  useImperativeHandle(ref, () => ({
    zoomBy: (delta) => { void flow.zoomTo(Math.max(MIN_ZOOM, Math.min(3, flow.getZoom() + delta))); },
    reset: () => { void flow.zoomTo(1); },
    fit: () => { void flow.fitView({ padding: 0.16, minZoom: MIN_ZOOM, maxZoom: 1 }); },
    arrange: () => setRevision((value) => value + 1),
  }), [flow]);

  const boxes = useMemo(() => nodes.map((node) => ({ id: node.id, ...node.position,
    width: node.data.layout.width, height: node.data.layout.height })), [nodes]);
  const routing = useEdgeRoutes(layout, boxes);
  // Порт как координата: угол карточки плюс смещение из раскладки. Ровно это
  // место вернул ELK и ровно его считает воркер маршрутов — мерить его третий
  // раз по DOM значит получить третий ответ.
  const anchor = useMemo(() => {
    const placed = new Map(boxes.map((box) => [box.id, box]));
    const offsets = new Map((layout?.nodes ?? []).flatMap((node) =>
      node.ports.map((port) => [`${node.id}:${port.id}`, port.y] as [string, number])));
    return (id: string, handle: string, side: "source" | "target"): Point | undefined => {
      const box = placed.get(id);
      if (!box) return undefined;
      return { x: side === "source" ? box.x + box.width : box.x,
        y: box.y + (offsets.get(`${id}:${handle}`) ?? box.height / 2) };
    };
  }, [boxes, layout]);
  const traversed = useMemo(() => {
    const path = executionPath(runtime), pairs = traversedEdges([START, ...path]);
    if (runtime.runStatus === "completed" && path.length) pairs.add(edgeKey(path[path.length - 1], END));
    return pairs;
  }, [runtime]);
  const edges: RoutedEdge[] = useMemo(() => (layout?.edges ?? []).map((edge) => {
    const source = topology.edges[Number(edge.id.slice(5))];
    const passed = traversed.has(edgeKey(edge.source, edge.target));
    const linked = selected === edge.source || selected === edge.target;
    const color = passed ? "var(--accent-orange)" : linked ? "var(--accent-teal)" : "var(--text-muted)";
    return {
      ...edge, type: "routed", selectable: false, focusable: false,
      data: { points: routing.routes?.[edge.id] ?? edge.points, boxes,
        deferred: routing.deferred && !routing.error,
        from: anchor(edge.source, edge.sourceHandle, "source"),
        to: anchor(edge.target, edge.targetHandle, "target") },
      label: linked ? source?.data : undefined,
      className: `canvas-edge${source?.conditional ? " conditional" : ""}${passed ? " traversed" : ""}${linked ? " linked" : ""}`,
      style: { stroke: color, strokeWidth: passed || linked ? 2 : 1.5,
        strokeDasharray: source?.conditional && !passed ? "5 5" : undefined,
        opacity: selected && !linked && !passed ? 0.25 : 1 },
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 16, height: 16 },
    };
  }), [layout, topology.edges, traversed, selected, boxes, anchor, routing.routes, routing.deferred, routing.error]);
  const needle = query.trim().toLowerCase();
  const decorated = useMemo(() => nodes.map((node) => {
    const info = manifest.nodes?.[node.id], status = diagramStatus(runtime, node.id);
    const title = node.id === START ? "Старт" : node.id === END ? "Конец" : info?.title ?? node.id;
    return { ...node, selected: selected === node.id, ariaLabel: `${title}: ${status}`,
      data: { ...node.data, info, status, count: visitCount(runtime, node.id),
        dimmed: !!needle && !`${node.id} ${title}`.toLowerCase().includes(needle) } };
  }), [nodes, manifest.nodes, runtime, selected, needle]);

  return <div ref={container} className="graph graph-flow" role="group" aria-label="Схема графа" tabIndex={0}
    onKeyDownCapture={(event) => {
      if ((event.target as Element).closest("input, textarea")) return;
      const nodeId = (event.target as Element).closest<HTMLElement>(".react-flow__node")?.dataset.id;
      if (nodeId && (event.key === "Enter" || event.key === " ")) onSelect(selected === nodeId ? null : nodeId);
      else if (event.key === "+" || event.key === "=") void flow.zoomIn();
      else if (event.key === "-") void flow.zoomOut();
      else if (event.key === "0") void flow.zoomTo(1);
      else if (event.key === "Escape") onSelect(null);
      else return;
      event.preventDefault();
      event.stopPropagation();
    }}>
    <ReactFlow<CardNode, RoutedEdge>
      nodes={decorated} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
      onNodesChange={onNodesChange}
      onNodeClick={(_, node) => onSelect(selected === node.id ? null : node.id)}
      onPaneClick={() => { onSelect(null); container.current?.focus({ preventScroll: true }); }}
      onMove={(_, viewport) => { onZoom(viewport.zoom); band(container.current, viewport.zoom); }}
      minZoom={MIN_ZOOM} maxZoom={3} panOnDrag={[0, 1]} panOnScroll
      zoomOnScroll={false} zoomActivationKeyCode={["Control", "Meta"]} zoomOnPinch
      zoomOnDoubleClick={false} nodeDragThreshold={4} autoPanOnNodeDrag
      selectNodesOnDrag={false}
      nodesConnectable={false} edgesReconnectable={false} deleteKeyCode={null}
      multiSelectionKeyCode={null} selectionKeyCode={null}
      // Знак React Flow снят штатным флагом библиотеки: полотно — рабочая
      // область продукта, и подпись чужого продукта в её углу читается как
      // часть схемы. Лицензия пакета это разрешает (MIT).
      proOptions={{ hideAttribution: true }}
      ariaLabelConfig={{ "node.a11yDescription.default": "Enter — открыть узел. Стрелки — переместить выбранный узел.",
        "minimap.ariaLabel": "Навигация по графу" }}
    >
      <Background gap={22} size={1} color="var(--border-strong)" />
      {minimap ? <MiniMap pannable zoomable position="bottom-left" nodeColor={(node) =>
        node.data.status === "completed" ? "var(--success)" : node.data.status === "running" ? "var(--accent-blue)" : "var(--border-strong)"} /> : null}
      <Panel position="bottom-center" className="canvas-help">Тяните фон — обзор · Тяните узел — перемещение · Ctrl + колесо — масштаб</Panel>
    </ReactFlow>
    {!layout ? <div className="canvas-layout-state" role={error ? "alert" : "status"} title={error || undefined}>
      {error ? <>Не удалось построить схему. <button onClick={() => setRevision((value) => value + 1)}>Повторить</button></> : "Строим схему…"}
    </div> : !layout.nodes.length ? <div className="canvas-layout-state" role="status">В графе пока нет узлов.</div> : null}
  </div>;
}

export const GraphDiagram = memo(function GraphDiagram(props: Props) {
  return <ReactFlowProvider><DiagramScene {...props} /></ReactFlowProvider>;
});
