export interface RailTool {
	id: string;
	label: string;
	icon: string;
	disabled?: boolean;
	later?: boolean;
	reason?: string;
}

export interface RailGroup {
	id: string;
	label: string;
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
