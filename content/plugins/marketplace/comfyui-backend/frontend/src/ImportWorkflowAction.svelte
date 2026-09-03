<script>
	// Registered at the core `admin.presets.header-actions` slot (see
	// manifest.yml `hooks.frontend`). Plugin components can't import core
	// Svelte components (BaseModal, Button, IconButton, ...) - the compiled
	// dist bundles its own Svelte runtime and Tailwind isn't generated for
	// anything outside frontend/src - so the icon button reproduces
	// IconButton's ghost/sm look with the semantic tokens directly and the
	// modal reproduces BaseModal's shell the same way ImportWorkflowModal does.
	import ImportWorkflowModal from './ImportWorkflowModal.svelte';

	let { context = {} } = $props();

	let open = $state(false);
</script>

<button
	type="button"
	class="import-wf-trigger"
	title="Import ComfyUI workflow"
	aria-label="Import ComfyUI workflow"
	onclick={() => (open = true)}
>
	<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
		<path stroke-linecap="round" stroke-linejoin="round" d="M12 3v12m0 0l-4-4m4 4l4-4M5 17v2a2 2 0 002 2h10a2 2 0 002-2v-2" />
	</svg>
</button>

{#if open}
	<ImportWorkflowModal
		selectPreset={context.selectPreset}
		refreshPresets={context.refreshPresets}
		onClose={() => (open = false)}
	/>
{/if}

<style>
	.import-wf-trigger {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		min-width: 32px;
		min-height: 32px;
		padding: 6px;
		color: rgb(var(--fg-muted, 169 174 184));
		background: transparent;
		border: none;
		border-radius: 4px;
		cursor: pointer;
		transition: color 0.1s ease, background-color 0.1s ease;
	}

	.import-wf-trigger:hover {
		color: rgb(var(--fg, 232 234 237));
		background: rgb(var(--surface-3, 39 42 49) / 0.5);
	}
</style>
