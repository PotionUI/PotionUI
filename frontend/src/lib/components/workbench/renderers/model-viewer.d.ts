/**
 * `<model-viewer>` is a `customElements.define`'d web component from
 * `@google/model-viewer`, loaded client-only via dynamic `import()` in
 * `MeshPreview.svelte` (its module touches `HTMLElement`/`customElements` at
 * import time, which doesn't exist during SSR). Svelte's element typing
 * doesn't know this tag, so this ambient declaration is the documented
 * escape hatch: https://svelte.dev/docs/typescript#custom-elements
 */
declare namespace svelteHTML {
	interface IntrinsicElements {
		'model-viewer': {
			src?: string;
			alt?: string;
			exposure?: string;
			'shadow-intensity'?: string;
			'camera-controls'?: boolean;
			'touch-action'?: string;
			class?: string;
			style?: string;
			'on:load'?: (event: Event) => void;
			'on:error'?: (event: CustomEvent<{ type: string; sourceError?: Error }>) => void;
		};
	}
}
