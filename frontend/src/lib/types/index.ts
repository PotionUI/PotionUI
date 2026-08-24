// Barrel export for all type definitions.
// Note: history.ts and models.ts both export a `Tag` interface, api.ts/chat.ts both
// export `ToolExecution`, api.ts/models.ts both export `ModelFile`, and llm.ts/segments.ts
// both export `LLMConfig`; import from the specific module if you need to
// disambiguate (e.g. `import type { Tag } from '$lib/types/history'`).

export * from './api';
export * from './audio';
export * from './chat';
export * from './generation';
// history.ts exports Tag which conflicts with models.ts; export explicitly to avoid collision
export type {
	GenerationFile as HistoryGenerationFile,
	Tag as HistoryTag,
	GenerationHistoryItem,
	DatePreset,
	MediaType,
	GenerationHistoryFilters,
	HistoryPageState
} from './history';
export * from './llm';
export * from './models';
export * from './segments';
export * from './tabs';
export * from './tutorial';

// The wildcard re-exports above collide on a handful of names between
// modules; these explicit re-exports pick the winner for the bare name
// (matching each module's primary domain) and alias the other so both
// remain reachable from the barrel.
export type { ToolExecution } from './chat';
export type { ToolExecution as DocsToolExecution } from './api';
export type { ModelFile } from './models';
export type { ModelFile as DocsModelFile } from './api';
export type { Command as SegmentCommand } from './segments';
export type { LLMConfig } from './llm';
export type { LLMConfig as SegmentLLMConfig } from './segments';
