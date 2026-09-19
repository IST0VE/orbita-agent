/**
 * Реестр встроенных виджетов.
 *
 * Сами виджеты разложены по ответственности: простая подача, разговор и ввод,
 * подтверждение внешнего действия, выбор материалов, результат прогона. Здесь
 * остался только список — то, что движок читает, чтобы связать тип из
 * манифеста с компонентом.
 *
 * Разделение не косметическое. Подтверждение публикации и виджеты списка
 * задач правят разные изменения и по разным причинам; пока они лежали в одном
 * файле на тысячу строк, любая правка одного заставляла перечитывать другое.
 */
import type { WidgetDefinition } from "../../manifest/types";
import { WidgetErrorBoundary } from "../ErrorBoundary";

import {
  ApprovalWidget,
  DraftListWidget,
  JiraProjectWidget,
} from "./approval";
import {
  ChatInputWidget,
  FormWidget,
  MessagesWidget,
} from "./conversation";
import {
  OperatorNotesWidget,
  PauseWidget,
} from "./pause";
import {
  FilePickerWidget,
  TaskPickerWidget,
} from "./pickers";
import {
  CodeWidget,
  ErrorWidget,
  JsonWidget,
  KeyValueWidget,
  MarkdownWidget,
  MoneyWidget,
  NumberWidget,
  ProgressWidget,
  StatusWidget,
  TableWidget,
  TextWidget,
  UnknownWidget,
} from "./primitives";
import {
  ArtifactListWidget,
  CostSummaryWidget,
  DocumentDiffWidget,
  DocumentPreviewWidget,
  FileListWidget,
  IssueListWidget,
  PublicationWidget,
  PublishedListWidget,
  RunSummaryWidget,
  VerdictWidget,
} from "./results";

const boundary = WidgetErrorBoundary;
const definition = (
  type: string,
  component: WidgetDefinition["component"],
  modes: WidgetDefinition["modes"] = ["view"],
  ownHeader = false,
): WidgetDefinition => ({ type, version: "1.0.0", modes, component, errorBoundary: boundary, ownHeader });

export const BUILTIN_WIDGETS: WidgetDefinition[] = [
  definition("text", TextWidget), definition("markdown", MarkdownWidget), definition("code", CodeWidget),
  definition("json", JsonWidget), definition("table", TableWidget), definition("key-value", KeyValueWidget),
  definition("number", NumberWidget), definition("money", MoneyWidget), definition("progress", ProgressWidget),
  definition("status", StatusWidget), definition("messages", MessagesWidget),
  definition("chat-input", ChatInputWidget, ["input"], true), definition("form", FormWidget, ["input", "edit", "interrupt"]),
  definition("approval", ApprovalWidget, ["interrupt"]), definition("artifact-list", ArtifactListWidget),
  definition("pause", PauseWidget, ["interrupt"]), definition("operator-notes", OperatorNotesWidget),
  definition("draft-list", DraftListWidget, ["view"], true),
  definition("document-preview", DocumentPreviewWidget), definition("document-diff", DocumentDiffWidget),
  definition("file-list", FileListWidget), definition("task-picker", TaskPickerWidget, ["input"], true),
  definition("run-summary", RunSummaryWidget), definition("nt-verdict", VerdictWidget),
  definition("cost-summary", CostSummaryWidget), definition("publication", PublicationWidget),
  definition("jira-project", JiraProjectWidget, ["input"]), definition("issue-list", IssueListWidget),
  definition("file-picker", FilePickerWidget, ["input"]),
  definition("published-list", PublishedListWidget, ["view"], true),
  definition("error", ErrorWidget), definition("unknown", UnknownWidget),
];
