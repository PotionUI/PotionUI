<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { Button, EmptyState, SegmentedControl, Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api';
	import { toasts } from '$lib/stores/toast';
	import { confirmDialog } from '$lib/stores/confirm';
	import AdminTabShell from './AdminTabShell.svelte';
	import AutomationCard from '../../automations/components/AutomationCard.svelte';
	import AutomationTemplateCard from '../../automations/components/AutomationTemplateCard.svelte';
	import CreateAutomationModal from '../../automations/components/CreateAutomationModal.svelte';
	import ImportWarningsModal from './ImportWarningsModal.svelte';
	import type {
		Automation,
		AutomationExportEnvelope,
		AutomationImportWarning,
		AutomationTemplate
	} from '$lib/types/automations';

	type View = 'automations' | 'templates';

	let automations = $state<Automation[]>([]);
	let templates = $state<AutomationTemplate[]>([]);
	let loading = $state(true);
	let showCreateModal = $state(false);
	let importInput = $state<HTMLInputElement | null>(null);
	let importing = $state(false);
	let warningsModalAutomation = $state<Automation | null>(null);
	let importWarnings = $state<AutomationImportWarning[]>([]);
	let usingTemplateKey = $state<string | null>(null);
	let enabledAutomationsCount = $derived(automations.filter((a) => a.enabled).length);
	let view = $derived<View>(
		$page.url.searchParams.get('automationView') === 'templates' ? 'templates' : 'automations'
	);

	function setView(next: View) {
		if (next === view) return;
		const url = new URL($page.url);
		url.searchParams.set('tab', 'automations');
		if (next === 'automations') {
			url.searchParams.delete('automationView');
		} else {
			url.searchParams.set('automationView', next);
		}
		void goto(url, { keepFocus: true, noScroll: true });
	}

	async function loadAutomations() {
		loading = true;
		const [automationsResult, templatesResult] = await Promise.allSettled([
			api.listAutomations(),
			api.listAutomationTemplates()
		]);

		let automationsFailed = true;
		if (automationsResult.status === 'fulfilled' && automationsResult.value.success) {
			automations = automationsResult.value.data ?? [];
			automationsFailed = false;
		}

		let templatesFailed = true;
		if (templatesResult.status === 'fulfilled' && templatesResult.value.success) {
			templates = templatesResult.value.data ?? [];
			templatesFailed = false;
		}

		// Collapse both failures into one toast instead of stacking two.
		if (automationsFailed && templatesFailed) {
			toasts.error('Failed to load automations');
		} else if (automationsFailed) {
			toasts.error('Failed to load automations');
		} else if (templatesFailed) {
			toasts.error('Failed to load automation templates');
		}

		loading = false;
	}

	onMount(loadAutomations);

	function handleCreated(event: CustomEvent<Automation>) {
		automations = [event.detail, ...automations];
	}

	async function handleUseTemplate(template: AutomationTemplate) {
		if (!template.available || usingTemplateKey) return;
		usingTemplateKey = template.key;
		try {
			const response = await api.instantiateAutomationTemplate(template.key);
			if (!response.success || !response.data) {
				toasts.error(response.message || response.error || 'Failed to use automation template');
				return;
			}

			const { automation, warnings } = response.data;
			automations = [automation, ...automations];
			if (warnings.length > 0) {
				toasts.warning(
					`Created “${automation.name}” disabled with ${warnings.length} setup warning${warnings.length === 1 ? '' : 's'}`,
					7000
				);
			} else {
				toasts.success(`Created “${automation.name}” disabled`);
			}
			await goto(`/automations/${automation.id}`);
		} catch {
			toasts.error('Failed to use automation template');
		} finally {
			usingTemplateKey = null;
		}
	}

	async function handleToggleEnabled(automation: Automation) {
		try {
			const response = automation.enabled
				? await api.disableAutomation(automation.id)
				: await api.enableAutomation(automation.id);
			if (response.success && response.data) {
				automations = automations.map((a) => (a.id === automation.id ? response.data! : a));
			}
		} catch {
			toasts.error('Failed to update automation');
		}
	}

	async function handleRunNow(automation: Automation) {
		try {
			const response = await api.runAutomation(automation.id);
			if (response.success) {
				toasts.success(`Run started for "${automation.name}"`);
			} else {
				toasts.error(response.error || 'Failed to start run');
			}
		} catch {
			toasts.error('Failed to start run');
		}
	}

	async function handleDelete(automation: Automation) {
		if (
			!(await confirmDialog({
				title: `Delete "${automation.name}"?`,
				message: 'This cannot be undone.',
				variant: 'danger'
			}))
		)
			return;
		try {
			const response = await api.deleteAutomation(automation.id);
			if (response.success) {
				automations = automations.filter((a) => a.id !== automation.id);
			} else {
				toasts.error(response.error || 'Failed to delete automation');
			}
		} catch {
			toasts.error('Failed to delete automation');
		}
	}

	function handleImportClick() {
		importInput?.click();
	}

	async function handleImportFileChange(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;

		importing = true;
		try {
			const text = await file.text();
			let document: AutomationExportEnvelope;
			try {
				document = JSON.parse(text);
			} catch {
				toasts.error('That file is not valid JSON');
				return;
			}

			const response = await api.importAutomation(document);
			if (response.success && response.data) {
				const { automation, warnings } = response.data;
				automations = [automation, ...automations];
				if (warnings.length > 0) {
					warningsModalAutomation = automation;
					importWarnings = warnings;
				} else {
					toasts.success(`Imported "${automation.name}" (disabled)`);
				}
			} else {
				// `message` carries the human-readable reason (e.g. which node types
				// aren't installed); `error` is only a machine code like `invalid_import`.
				toasts.error(response.message || response.error || 'Failed to import automation');
			}
		} catch {
			toasts.error('Failed to import automation');
		} finally {
			importing = false;
			// Reset so re-picking the same file still fires `change`.
			input.value = '';
		}
	}

	function handleCloseWarningsModal() {
		warningsModalAutomation = null;
		importWarnings = [];
	}
</script>

<div class="space-y-4">
	<SegmentedControl
		items={[
			{ id: 'automations', label: 'Automations', icon: 'bolt', count: loading ? undefined : automations.length },
			{ id: 'templates', label: 'Templates', icon: 'copy', count: loading ? undefined : templates.length }
		]}
		selected={view}
		onSelect={(id) => setView(id as View)}
		ariaLabel="Automation views"
	/>

	<AdminTabShell
		title="Automations"
		icon="bolt"
		counts={[
			{ label: automations.length === 1 ? 'automation' : 'automations', value: automations.length },
			{ label: 'enabled', value: enabledAutomationsCount, tone: 'success' }
		]}
	>
	{#snippet actions()}
		{#if view === 'automations'}
			<input
				bind:this={importInput}
				type="file"
				accept="application/json,.json"
				class="hidden"
				onchange={handleImportFileChange}
			/>
			<Button
				variant="ghost"
				size="sm"
				icon="upload"
				onclick={handleImportClick}
				loading={importing}
			>
				Import
			</Button>
			<Button
				variant="primary"
				size="sm"
				icon="plus"
				onclick={() => (showCreateModal = true)}
			>
				New Automation
			</Button>
		{/if}
	{/snippet}
	</AdminTabShell>

	{#if loading}
		<div class="flex items-center justify-center py-16">
			<Spinner />
		</div>
	{:else if view === 'templates'}
		<section>
			{#if templates.length === 0}
				<EmptyState
					icon="copy"
					title="No templates available"
					description="Enable a plugin that contributes automation templates to see ready-made workflows here."
				/>
			{:else}
				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
					{#each templates as template (template.key)}
						<AutomationTemplateCard
							{template}
							loading={usingTemplateKey === template.key}
							onUse={handleUseTemplate}
						/>
					{/each}
				</div>
			{/if}
		</section>
	{:else}
		<section>
			{#if automations.length === 0}
				<EmptyState
					icon="bolt"
					title="No automations yet"
					description="An automation runs a workflow for you automatically — on a schedule or when something happens — so you don't have to trigger it by hand."
				>
					{#snippet actions()}
						<Button variant="primary" icon="plus" onclick={() => (showCreateModal = true)}>
							Create new automation
						</Button>
						<Button variant="secondary" icon="copy" onclick={() => setView('templates')}>
							Browse templates
						</Button>
					{/snippet}
				</EmptyState>
			{:else}
				<div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
					{#each automations as automation (automation.id)}
						<AutomationCard
							{automation}
							onToggleEnabled={handleToggleEnabled}
							onRunNow={handleRunNow}
							onDelete={handleDelete}
						/>
					{/each}
				</div>
			{/if}
		</section>
	{/if}
</div>

<CreateAutomationModal
	isOpen={showCreateModal}
	on:close={() => (showCreateModal = false)}
	on:created={handleCreated}
/>

<ImportWarningsModal
	isOpen={warningsModalAutomation !== null}
	automationName={warningsModalAutomation?.name ?? ''}
	warnings={importWarnings}
	on:close={handleCloseWarningsModal}
/>
