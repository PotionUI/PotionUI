/**
 * Wireframe toggle for a `<model-viewer>` element.
 *
 * model-viewer's public Scene-graph API (`element.model.materials`) exposes
 * PBR factors and textures but deliberately stops short of raw three.js
 * Material properties - there is no public `wireframe` setter anywhere in
 * its documented surface. The only way to reach the real THREE.Material
 * instances is `element[$scene]`, a `unique symbol`-keyed field that IS
 * exported from `model-viewer-base.js` (unlike most of the library's
 * internals, whose symbols are module-private and not importable at all) and
 * has been present, unchanged, since $scene's introduction. That's the one
 * documented-ish, version-stable seam available, so this module deep-imports
 * it rather than shipping a second three.js renderer just for one boolean.
 *
 * Every access here is defensive: a future model-viewer release is free to
 * rename or remove `$scene`, at which point `isWireframeSupported` starts
 * returning false and the toggle hides itself instead of throwing.
 */
import { logger } from '$lib/utils/logger';

type ModelViewerScene = {
	traverse: (callback: (object: unknown) => void) => void;
};

let scenePropertySymbol: symbol | null | undefined; // undefined = not looked up yet
let needsRenderSymbol: symbol | null | undefined;
let loadFailed = false;

async function loadInternals(): Promise<void> {
	if (scenePropertySymbol !== undefined || loadFailed) return;
	try {
		const mod = await import('@google/model-viewer/lib/model-viewer-base.js');
		scenePropertySymbol = (mod as any).$scene ?? null;
		needsRenderSymbol = (mod as any).$needsRender ?? null;
	} catch (err) {
		loadFailed = true;
		scenePropertySymbol = null;
		needsRenderSymbol = null;
		logger.warn('[modelViewerWireframe] could not load model-viewer internals; wireframe toggle unavailable', err);
	}
}

function getScene(element: HTMLElement): ModelViewerScene | null {
	if (!scenePropertySymbol) return null;
	const scene = (element as any)[scenePropertySymbol];
	return scene && typeof scene.traverse === 'function' ? scene : null;
}

/** Call once the element's model has loaded, before the first `setWireframe`. */
export async function prepareWireframeSupport(element: HTMLElement): Promise<boolean> {
	await loadInternals();
	return getScene(element) !== null;
}

/** Synchronous check for callers that already awaited `prepareWireframeSupport`. */
export function isWireframeSupported(element: HTMLElement): boolean {
	return getScene(element) !== null;
}

/**
 * Toggles wireframe on every mesh material in the loaded scene. Returns
 * whether it actually found anything to toggle (false means the button
 * should disable itself - this model-viewer version's internals moved).
 */
export function setWireframe(element: HTMLElement, enabled: boolean): boolean {
	const scene = getScene(element);
	if (!scene) return false;

	let touched = false;
	scene.traverse((object: any) => {
		const material = object?.material;
		if (!material) return;
		const materials = Array.isArray(material) ? material : [material];
		for (const m of materials) {
			if (m && typeof m === 'object' && 'wireframe' in m) {
				m.wireframe = enabled;
				touched = true;
			}
		}
	});

	if (touched && needsRenderSymbol) {
		(element as any)[needsRenderSymbol]?.();
	}
	return touched;
}
