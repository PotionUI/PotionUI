import type { Component } from 'svelte';
import type { MediaRef, RunRef, ToolTab, ToolTabHint, ToolTabStatus } from '$lib/types/tabs';

export type { ToolTabHint, ToolTabStatus };

export type CoreToolKind = 'image' | 'video' | 'elixir';

export type ToolKind = CoreToolKind | `${string}.${string}`;

export type ToolMediaKind = 'image' | 'video' | 'audio';

export interface ToolTabOpenOptions {
	activeTool?: string | null;
	hint?: ToolTabHint | null;
	key?: string | null;
	state?: unknown;
	name?: string | null;
}

export interface ToolTabAction {
	label?: string;
	disabled?: boolean;
	run: () => void;
}

export interface ToolTabSaveAction extends ToolTabAction {
	progress?: number | null;
}

export interface ToolTabActions {
	undo?: ToolTabAction | null;
	redo?: ToolTabAction | null;
	save?: ToolTabSaveAction | null;
}

export interface ToolTabHost<State = unknown> {
	readonly tabId: string;
	readonly tool: string;
	readonly source: MediaRef | null;
	readonly state: State | null;
	readonly activeTool: string | null;
	readonly run: RunRef | null;
	readonly hint: ToolTabHint | null;
	readonly phone: boolean;
	readonly key?: string | null;
	setState(next: State | null): void;
	setDirty(edits: number): void;
	setTitle(title: string): void;
	setThumbnail(url: string | null): void;
	setSource(source: MediaRef | null): void;
	replaceSource(source: MediaRef): void;
	setActiveTool(activeTool: string | null): void;
	setRun(run: RunRef | null): void;
	clearHint(): void;
	setActions(actions: ToolTabActions | null): void;
	setStatus?(status: ToolTabStatus | null): void;
	claimKey?(key: string, state: State | null, title?: string | null): string;
	openToolTab(tool: string, source: MediaRef | null, options?: ToolTabOpenOptions): string;
}

export interface ToolTabComponentProps {
	host: ToolTabHost;
}

export type ToolTabComponent = Component<ToolTabComponentProps>;

export interface ToolKindMenuProps {
	close: () => void;
	back?: () => boolean;
	phone?: boolean;
}

export type ToolKindMenuComponent = Component<ToolKindMenuProps>;

export interface ToolTabWatchContext {
	tab: ToolTab;
	setStatus(status: ToolTabStatus | null): void;
}

export type ToolTabWatcher = (context: ToolTabWatchContext) => () => void;

export type ToolTabComponentLoader = () => Promise<ToolTabComponent | { default: ToolTabComponent } | null>;

export interface ToolKindDefinition {
	tool: ToolKind;
	label: string;
	description?: string;
	icon: string;
	accepts: ToolMediaKind[];
	handles?: string[];
	component?: ToolTabComponent;
	load?: ToolTabComponentLoader;
	pluginId?: string;
	order?: number;
	menu?: () => Promise<ToolKindMenuComponent | { default: ToolKindMenuComponent }>;
	watch?: ToolTabWatcher;
	confirmClose?: boolean;
	renamable?: boolean;
	phoneActions?: boolean;
	onClosed?: (tab: ToolTab) => void;
}

export interface PluginToolTabEntry {
	plugin_id: string;
	id: string;
	tool: string;
	label: string;
	icon?: string | null;
	accepts?: ToolMediaKind[];
	handles?: string[];
	component: string;
}
