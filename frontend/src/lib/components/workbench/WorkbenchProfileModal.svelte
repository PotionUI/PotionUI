<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Button, Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api';
	import { logger } from '$lib/utils/logger';
	import { copyText } from '$lib/utils/clipboard';

	export let isOpen: boolean = false;
	export let generationId: string | null = null;

	const dispatch = createEventDispatcher<{ close: void }>();

	let report = '';
	let loading = false;
	let errorMessage = '';
	let copied = false;
	// Which generation's report is currently loaded, so reopening the same one
	// doesn't refetch and a new one does.
	let loadedFor: string | null = null;

	// Fetch the rendered report the first time this modal opens for a given
	// generation. The backend endpoint is admin-only; a non-admin never gets here
	// because the trigger button is admin-gated.
	$: if (isOpen && generationId && generationId !== loadedFor && !loading) {
		fetchReport(generationId);
	}

	async function fetchReport(id: string) {
		loading = true;
		errorMessage = '';
		report = '';
		try {
			report = await api.getGenerationProfileReport(id);
			loadedFor = id;
		} catch (error) {
			logger.error('Error fetching generation profile report:', error);
			errorMessage = 'Could not load the profile report for this generation.';
		} finally {
			loading = false;
		}
	}

	async function copyReport() {
		if (!report) return;
		const ok = await copyText(report);
		if (ok) {
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} else {
			logger.error('Error copying profile report');
		}
	}

	async function downloadRaw() {
		if (!generationId) return;
		try {
			const url = `${api.getBaseURL()}/api/generations/${generationId}/profile`;
			const token = api.getToken();
			const resp = await fetch(url, {
				credentials: 'include',
				headers: token ? { Authorization: `Bearer ${token}` } : {}
			});
			if (!resp.ok) {
				logger.error('Profile download failed:', resp.status);
				return;
			}
			const blob = await resp.blob();
			const objectUrl = URL.createObjectURL(blob);
			const link = document.createElement('a');
			link.href = objectUrl;
			link.download = `profile-${generationId}.jsonl`;
			link.click();
			URL.revokeObjectURL(objectUrl);
		} catch (error) {
			logger.error('Error downloading profile jsonl:', error);
		}
	}
</script>

<BaseModal
	{isOpen}
	title="Resource Profile"
	sizeClass="md:w-[900px] md:max-w-[92vw]"
	on:close={() => dispatch('close')}
>
	<svelte:fragment slot="headerIcon">
		<Icon name="document" className="w-5 h-5 text-fg-muted flex-shrink-0" />
	</svelte:fragment>

	<div class="p-5 space-y-4">
		{#if loading}
			<div class="flex items-center justify-center gap-2 py-16 text-fg-subtle">
				<Spinner size="sm" />
				<span class="text-sm">Rendering profile report…</span>
			</div>
		{:else if errorMessage}
			<div class="text-sm text-danger py-10 text-center">{errorMessage}</div>
		{:else}
			<div class="flex items-center justify-end gap-2">
				<Button variant="secondary" size="sm" icon="copy" onclick={copyReport}>
					{copied ? 'Copied' : 'Copy report'}
				</Button>
				<Button variant="secondary" size="sm" icon="download" onclick={downloadRaw}>
					profile.jsonl
				</Button>
			</div>
			<pre
				class="select-text bg-surface-2 text-fg text-xs font-mono tabular-nums leading-relaxed rounded-lg border border-line p-4 max-h-[60vh] overflow-auto whitespace-pre">{report}</pre>
		{/if}
	</div>
</BaseModal>
