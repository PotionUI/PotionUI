<script lang="ts">
	// Onboarding-only slice of the models-location setting: the global
	// external directory, no per-type overrides (those stay admin-only, see
	// ModelsLocationPanel.svelte). Skippable - "Skip" and a successful
	// "Apply" both collapse the step so it doesn't keep nagging on repeat
	// visits to /setup, while a "Change" link always reopens it.
	import { onMount } from 'svelte';
	import { browser } from '$app/environment';
	import { toasts } from '$lib/stores/toast';
	import { Button, Input, Alert, Spinner, Card } from '$lib/components/ui';
	import { ModelsLocationState } from '$lib/models-location/state.svelte';

	const DISMISSED_STORAGE_KEY = 'potionui:setup:modelsLocationDismissed';

	const location = new ModelsLocationState();

	let externalPath = $state('');
	let dismissed = $state(false);

	onMount(async () => {
		if (browser) {
			try {
				dismissed = localStorage.getItem(DISMISSED_STORAGE_KEY) === '1';
			} catch {
				// localStorage may be unavailable - the step just stays expanded.
			}
		}
		await location.load();
		externalPath = location.config?.external_path ?? '';
	});

	function persistDismissed(value: boolean) {
		dismissed = value;
		if (!browser) return;
		try {
			if (value) {
				localStorage.setItem(DISMISSED_STORAGE_KEY, '1');
			} else {
				localStorage.removeItem(DISMISSED_STORAGE_KEY);
			}
		} catch {
			// ignore - dismissal just won't survive a reload
		}
	}

	function currentPathLabel(): string {
		const path = location.config?.external_path;
		return path ? path : 'models/ (default location in this install)';
	}

	async function apply() {
		const ok = await location.apply(externalPath);
		if (ok) {
			toasts.success('Models location applied. Re-indexing in the background.');
			persistDismissed(true);
		}
	}

	function skip() {
		persistDismissed(true);
	}
</script>

{#if dismissed}
	<div class="flex items-center justify-between gap-3 text-sm text-fg-muted">
		<span
			>Models location: <span class="font-mono text-fg">{currentPathLabel()}</span></span
		>
		<button type="button" class="text-xs text-fg-muted hover:text-fg" onclick={() => persistDismissed(false)}>
			Change
		</button>
	</div>
{:else}
	<Card class="space-y-3">
		<div>
			<h2 class="text-sm font-semibold text-fg">Models location</h2>
			<p class="text-sm text-fg-muted mt-0.5">
				Point PotionUI at an external directory for model files before anything downloads.
				You can change this later in Admin - System Settings.
			</p>
		</div>

		{#if location.loading}
			<Spinner size="sm" />
		{:else}
			{#if location.config?.windows_unsupported}
				<Alert variant="warning" icon
					>Relocating the models directory isn't supported on Windows yet. Skip this step and
					use the default location.</Alert
				>
			{/if}

			{#if location.error}
				<Alert variant="danger" icon>{location.error}</Alert>
			{/if}

			<p class="text-xs text-fg-subtle">
				Currently: <span class="font-mono text-fg-muted">{currentPathLabel()}</span>
			</p>

			<label for="setup-models-external-path" class="block text-sm font-medium text-fg mb-1"
				>External directory</label
			>
			<Input
				id="setup-models-external-path"
				bind:value={externalPath}
				placeholder="/mnt/storage/models"
				disabled={location.applying}
			/>

			<div class="flex items-center gap-3 mt-1">
				<Button variant="primary" size="sm" onclick={apply} loading={location.applying} disabled={location.applying}
					>Apply</Button
				>
				<Button variant="secondary" size="sm" onclick={skip} disabled={location.applying}>Skip</Button>
			</div>
		{/if}
	</Card>
{/if}
