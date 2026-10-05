export type SetupStepStatus = 'done' | 'todo' | 'waiting';
export type SetupActionKind = 'link' | 'restart' | 'settings';

export interface SetupAction {
	kind: SetupActionKind;
	label: string;
	href?: string | null;
}

export interface SetupStep {
	id: string;
	kind: string;
	label: string;
	description: string;
	status: SetupStepStatus;
	action?: SetupAction | null;
}

export interface PluginSetupReport {
	plugin_id: string;
	complete: boolean;
	remaining: number;
	steps: SetupStep[];
	guide?: { title: string; href: string } | null;
}

export type PluginSetupMap = Record<string, PluginSetupReport>;

export function setupMapFrom(reports: PluginSetupReport[]): PluginSetupMap {
	return Object.fromEntries(reports.map((report) => [report.plugin_id, report]));
}

export function nextSetupStep(report: PluginSetupReport): SetupStep | null {
	return report.steps.find((step) => step.status === 'todo') ?? null;
}

export function setupProgressLabel(report: PluginSetupReport): string {
	const done = report.steps.length - report.remaining;
	return `${done} of ${report.steps.length} done`;
}

export function setupPendingFor(plugin: { id: string; enabled: boolean }, reports: PluginSetupMap): PluginSetupReport | null {
	const report = reports[plugin.id];
	return plugin.enabled && report && !report.complete ? report : null;
}
