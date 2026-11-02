import type { Component } from 'svelte';
import type { MediaRef, RunRef } from '$lib/types/tabs';

export type CoreToolKind = 'image' | 'video';

export type ToolKind = CoreToolKind | `${string}.${string}`;

export type ToolMediaKind = 'image' | 'video' | 'audio';

export interface ToolTabOpenOptions {
	activeTool?: string | null;
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
	readonly phone: boolean;
	setState(next: State | null): void;
	setDirty(edits: number): void;
	setTitle(title: string): void;
	setThumbnail(url: string | null): void;
	setSource(source: MediaRef | null): void;
	setActiveTool(activeTool: string | null): void;
	setRun(run: RunRef | null): void;
	setActions(actions: ToolTabActions | null): void;
	openToolTab(tool: string, source: MediaRef | null, options?: ToolTabOpenOptions): string;
}

export interface ToolTabComponentProps {
	host: ToolTabHost;
}

export type ToolTabComponent = Component<ToolTabComponentProps>;

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
