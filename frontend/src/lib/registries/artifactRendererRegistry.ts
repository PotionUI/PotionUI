import { createRegistry, CORE_OWNER, pluginOwner } from './registry';
import { resolveLazyEntry } from './lazyResolve';

/**
 * artifact_type -> Svelte component. Every registered component receives
 * `{ artifact, onSeedClick }` props (onSeedClick is optional and only used by
 * the seed artifact today; kept uniform so <svelte:component> instantiation
 * in GenerationPanelHistory stays generic).
 *
 * An entry is either a `static` core component (registered eagerly by
 * `lib/generation/artifacts/builtin.ts`) or a `lazy` plugin-hosted component
 * (registered from a manifest's `renderers: [{kind: "history.artifact", ...}]`
 * entry), resolved on demand via `plugin-api/componentResolver` - mirrors
 * `fields/registry.ts`'s static/lazy shape.
 */
export type ArtifactRendererEntry =
	| { kind: 'static'; component: any }
	| { kind: 'lazy'; pluginId: string; asset: string };

const registry = createRegistry<ArtifactRendererEntry>('artifact-renderer');

const resolvedCache = new Map<string, any | null>();

function registerArtifactRenderer(
	artifactType: string,
	entry: { component: any } | { pluginId: string; asset: string }
): void {
	if ('component' in entry) {
		registry.register(artifactType, { kind: 'static', component: entry.component }, CORE_OWNER);
	} else {
		registry.register(
			artifactType,
			{ kind: 'lazy', pluginId: entry.pluginId, asset: entry.asset },
			pluginOwner(entry.pluginId)
		);
		resolvedCache.delete(artifactType);
	}
}

function unregisterArtifactRenderer(artifactType: string, owner?: string): void {
	registry.unregister(artifactType, owner);
	resolvedCache.delete(artifactType);
}

/** Resolve `artifactType` to its component. Returns `null` if unregistered. Publication rules in `lazyResolve.ts`. */
function resolveArtifactRenderer(artifactType: string): Promise<any | null> {
	return resolveLazyEntry(registry, resolvedCache, artifactType);
}

/**
 * Public registry surface. Kept as a single exported object (rather than
 * loose functions) since it's also exposed on `window.__potionui` for
 * plugins - `register` is the legacy sync-static-only call some plugins may
 * already use, `registerRenderer` is the new dual static/lazy entry point.
 */
export const artifactRendererRegistry = {
	register: registerArtifactRenderer,
	unregister: unregisterArtifactRenderer,
	resolve: resolveArtifactRenderer,
	has: (artifactType: string) => registry.has(artifactType),
	keys: () => registry.keys()
};
