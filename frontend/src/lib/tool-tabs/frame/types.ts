export interface RailTool {
	id: string;
	label: string;
	icon: string;
	keys?: string;
	ai?: boolean;
	disabled?: boolean;
	later?: boolean;
	reason?: string;
}

export interface RailGroup {
	id: string;
	label: string;
	fold?: number;
	tools: RailTool[];
}

export interface SourceInfo {
	label: string;
	icon: string;
}

export interface SendTarget {
	id: string;
	name: string;
	detail: string;
	color?: string;
	badge?: string;
}

export interface ReplaceOption {
	allowed: boolean;
	reason?: string;
}

export interface SavingProgress {
	percent: number;
	label?: string;
	caption?: string;
}

export type SaveAction =
	| { kind: 'save' }
	| { kind: 'save-new' }
	| { kind: 'save-sheet' }
	| { kind: 'replace' }
	| { kind: 'send'; target: string }
	| { kind: 'new-generation' }
	| { kind: 'download' }
	| { kind: 'copy' };

export type StackVariant = 'panel' | 'sheet';

export interface LiveLayer {
	label: string;
	hidden: boolean;
	opacity: number;
	onhide: () => void;
	onopacity: (value: number) => void;
}

export interface StackProgress {
	target: string | null;
	after?: string | null;
	live?: LiveLayer | null;
}
