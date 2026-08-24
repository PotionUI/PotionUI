/**
 * Fetches `/api/plugins/frontend-extensions` once at app init (manifest-derived
 * `renderers:` + `contributions:` from enabled plugins) and:
 *  - registers `history.artifact` renderers into `artifactRendererRegistry`
 *  - registers `workbench.file` renderers into `workbenchFileRendererRegistry`
 *  - registers `model.view` renderers into `modelViewRegistry`
 *  - registers `generation.output` renderers as generation-message handlers
 *    (`lib/generation/messages/pluginOutput.ts`)
 *  - feeds `contributions` into `lib/extensions/extensionSlots.ts`
 *
 * Non-fatal on failure, mirroring `stores/fieldTypes.ts`.
 */
import { logger } from '$lib/utils/logger';
import { api } from '$lib/services/api/index';
import { artifactRendererRegistry } from '$lib/registries/artifactRendererRegistry';
import { registerWorkbenchFileRenderer } from '$lib/registries/workbenchFileRendererRegistry';
import { registerModelView } from '$lib/registries/modelViewRegistry';
import { chatToolRendererRegistry } from '$lib/registries/chatToolRendererRegistry';
import { registerPluginOutputHandler } from '$lib/generation/messages/pluginOutput';
import { setContributions, type SlotContribution } from '$lib/extensions/extensionSlots';

interface FrontendExtensionRenderer {
	plugin_id: string;
	kind: string;
	key: string;
	component: string;
}

let initialized = false;

export async function initExtensions(): Promise<void> {
	if (initialized) return;
	initialized = true;

	try {
		const response = await api.getClient().get('/api/plugins/frontend-extensions');
		const data = response.data;

		if (!data?.success) {
			logger.warn('Failed to load frontend extensions:', data?.message);
			return;
		}

		const renderers: FrontendExtensionRenderer[] = data.data?.renderers || [];
		const contributions: SlotContribution[] = data.data?.contributions || [];

		for (const renderer of renderers) {
			if (!renderer.key || !renderer.component) continue;
			const entry = { pluginId: renderer.plugin_id, asset: renderer.component };

			switch (renderer.kind) {
				case 'history.artifact':
					artifactRendererRegistry.register(renderer.key, entry);
					break;
				case 'workbench.file':
					registerWorkbenchFileRenderer(renderer.key, entry);
					break;
				case 'model.view':
					registerModelView(renderer.plugin_id, renderer.key, renderer.component);
					break;
				case 'generation.output':
					registerPluginOutputHandler(renderer.key, renderer.plugin_id, renderer.component);
					break;
				case 'chat.tool':
					chatToolRendererRegistry.register(renderer.key, entry);
					break;
				default:
					logger.warn(`Unknown renderer kind "${renderer.kind}" from plugin ${renderer.plugin_id}`);
			}
		}

		setContributions(contributions);
	} catch (err) {
		// Non-fatal: core renderers/slots still work without plugin extensions.
		logger.error('Failed to initialize frontend extensions:', err);
	}
}
