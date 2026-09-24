export interface StepKindDisplay {
	icon: string;
	description: string;
}

const STEP_KIND_DISPLAY: Record<string, StepKindDisplay> = {
	'plugins.ensure': { icon: 'extension', description: 'Enables the plugin this recipe needs.' },
	'backend.detect': { icon: 'search', description: 'Looks for a backend that can run this recipe.' },
	'backend.ensure': { icon: 'server', description: 'Configures the backend this recipe runs on.' },
	'models.index': { icon: 'database', description: 'Indexes the models already on disk.' },
	'models.index_backend': { icon: 'database', description: 'Indexes the models on the backend.' },
	'preset.ensure': { icon: 'layers', description: 'Installs the preset this recipe sets up.' },
	'pipeline.render': { icon: 'cpu', description: 'Builds the pipeline used to test the preset.' },
	'artifacts.plan': { icon: 'clipboard-list', description: 'Works out which models to download.' },
	'artifacts.fetch': { icon: 'download', description: 'Downloads the models this recipe needs.' },
	'generation.smoke': { icon: 'wand', description: 'Runs a real generation to prove it all works.' },
	'workspace.activate': { icon: 'check', description: "Switches to this recipe's workspace." }
};

const DEFAULT_STEP_KIND_DISPLAY: StepKindDisplay = { icon: 'settings', description: 'Runs this step.' };

export function stepKindDisplay(kind: string): StepKindDisplay {
	return STEP_KIND_DISPLAY[kind] ?? DEFAULT_STEP_KIND_DISPLAY;
}
