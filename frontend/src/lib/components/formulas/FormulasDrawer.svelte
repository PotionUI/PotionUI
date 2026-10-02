<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import DrawerShell from '$lib/components/drawer/DrawerShell.svelte';
	import DrawerHeader from '$lib/components/drawer/DrawerHeader.svelte';
	import DrawerSearch from '$lib/components/drawer/DrawerSearch.svelte';
	import DrawerGroupHead from '$lib/components/drawer/DrawerGroupHead.svelte';
	import DrawerRow from '$lib/components/drawer/DrawerRow.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, Spinner } from '$lib/components/ui';
	import FormulaRowMenu from './FormulaRowMenu.svelte';
	import FormulaSaveView from './FormulaSaveView.svelte';
	import FormulaApplyView from './FormulaApplyView.svelte';
	import FormulaAppliedBar from './FormulaAppliedBar.svelte';
	import { api } from '$lib/services/api/index';
	import { formulaErrorMessage } from '$lib/formulas/errors';
	import { dependencyMap, flatDefaults, indexFields } from '$lib/formulas/fieldIndex';
	import { buildCapabilityCheck } from '$lib/formulas/capabilityCheck';
	import { buildDraft, filterFormulas, groupStates, preselectedIds } from '$lib/formulas/groups';
	import { appliedKey, applyPlan, planApply, type ApplyPlan } from '$lib/formulas/planApply';
	import { loadFormSchema } from '$lib/formulas/schemaLoader';
	import type {
		Formula,
		FormulaDeclaration,
		LoraMode,
		MenuAction,
		ServerPlan
	} from '$lib/formulas/types';
	import { formulaApplied } from '$lib/stores/formulaApplied';
	import { formulaLists, formulasKey, loadFormulas, removeFormula, upsertFormula } from '$lib/stores/formulas';
	import { confirmDialog } from '$lib/stores/confirm';
	import { tabsStore } from '$lib/stores/tabs';
	import { fullDateTime } from '$lib/session/sessionDrawerModel';
	import { parseServerDate } from '$lib/utils/relativeTime';
	import type { Tab } from '$lib/types/tabs';

	const KEEP_OPEN_KEY = 'formulas-drawer-keep-open';

	let {
		tab,
		declaration,
		presetName,
		presetVersion,
		modeLabel,
		onClose,
		onApplied
	}: {
		tab: Tab;
		declaration: FormulaDeclaration;
		presetName: string;
		presetVersion: string;
		modeLabel: string;
		onClose: () => void;
		onApplied?: () => void;
	} = $props();

	type View = 'list' | 'save' | 'apply';

	let view = $state<View>('list');
	let query = $state('');
	let searchEl = $state<HTMLInputElement>();
	let schema = $state.raw<unknown>(null);
	let schemaError = $state<string | null>(null);
	let schemaAttempt = $state(0);
	let savedId = $state<string | null>(null);
	let listNotice = $state<string | null>(null);
	let editing = $state<Formula | null>(null);
	let saving = $state(false);
	let saveError = $state<string | null>(null);
	let renamingId = $state<string | null>(null);
	let renameValue = $state('');
	let applying = $state<Formula | null>(null);
	let serverPlan = $state.raw<ServerPlan | null>(null);
	let planLoading = $state(false);
	let planError = $state<string | null>(null);
	let loraMode = $state<LoraMode>('replace');
	let excluded = $state<Set<string>>(new Set());
	let planRequest = 0;

	const key = $derived(formulasKey(tab.selectedPreset ?? '', tab.selectedMode ?? ''));
	const list = $derived($formulaLists[key]);
	const items = $derived(list?.items ?? []);
	const filtered = $derived(filterFormulas(items, query));
	const index = $derived(indexFields(schema));
	const defaults = $derived(schema ? flatDefaults(schema) : {});
	const states = $derived(groupStates(declaration, index, tab.formData ?? {}, defaults));
	const differing = $derived(states.filter((state) => state.changed).length);
	const groupOrder = $derived(declaration.groups.map((group) => group.id));
	const schemaReady = $derived(schema !== null && !schemaError);
	const plan = $derived<ApplyPlan | null>(
		serverPlan
			? planApply(serverPlan, {
					dependencies: dependencyMap(index),
					capabilityInvalid: buildCapabilityCheck(schema, tab.formData ?? {})
				})
			: null
	);
	const selected = $derived(
		new Set(
			(plan?.changes ?? [])
				.filter((change) => !change.companionOf && !excluded.has(change.field))
				.map((change) => change.field)
		)
	);

	onMount(() => {
		if (!list?.loaded) void loadFormulas(tab.selectedPreset ?? '', tab.selectedMode ?? '');
	});

	$effect(() => {
		const presetId = tab.selectedPreset;
		const mode = tab.selectedMode;
		const variant = tab.selectedVariant;
		const attempt = schemaAttempt;
		if (!presetId || !mode) return;
		let live = true;
		schemaError = null;
		loadFormSchema(presetId, mode, variant, attempt > 0).then(
			(loaded) => {
				if (live) schema = loaded;
			},
			(error) => {
				if (!live) return;
				schema = null;
				schemaError = formulaErrorMessage(error, 'Could not load this form.');
			}
		);
		return () => {
			live = false;
		};
	});

	let flashTimer: ReturnType<typeof setTimeout> | undefined;

	function flashRow(id: string) {
		savedId = id;
		clearTimeout(flashTimer);
		flashTimer = setTimeout(() => (savedId = null), 2500);
	}

	onDestroy(() => clearTimeout(flashTimer));

	function backToList() {
		view = 'list';
		editing = null;
		applying = null;
		saveError = null;
		serverPlan = null;
		planError = null;
		planRequest += 1;
	}

	function handleEscape(): boolean {
		if (renamingId) {
			renamingId = null;
			return true;
		}
		if (query) {
			query = '';
			return true;
		}
		if (view !== 'list') {
			backToList();
			return true;
		}
		return false;
	}

	function startSave() {
		listNotice = null;
		editing = null;
		saveError = null;
		view = 'save';
	}

	async function runSave(name: string, chosen: Set<string>) {
		const draft = buildDraft({
			presetId: tab.selectedPreset ?? '',
			mode: tab.selectedMode ?? '',
			variant: tab.selectedVariant ?? null,
			presetVersion,
			name,
			declaration,
			selected: chosen,
			index,
			formData: tab.formData ?? {}
		});
		saving = true;
		saveError = null;
		try {
			const response = editing
				? await api.updateFormula(editing.id, {
						name: draft.name,
						content: {
							variant: draft.variant,
							groups: draft.groups,
							values: draft.values,
							preset_version: draft.preset_version
						}
					})
				: await api.createFormula(draft);
			if (!response.success || !response.data) throw new Error(response.error || 'Could not save the formula.');
			upsertFormula(response.data);
			backToList();
			flashRow(response.data.id);
		} catch (error) {
			saveError = formulaErrorMessage(error, 'Could not save the formula.');
		} finally {
			saving = false;
		}
	}

	async function fetchPlan(formula: Formula) {
		const request = ++planRequest;
		planLoading = true;
		planError = null;
		try {
			const response = await api.planFormula(formula.id, {
				form_name: tab.selectedVariant ?? null,
				current_values: tab.formData ?? {},
				lora_mode: loraMode
			});
			if (request !== planRequest) return;
			if (!response.success || !response.data) throw new Error(response.error || 'Could not check this formula.');
			serverPlan = response.data;
		} catch (error) {
			if (request !== planRequest) return;
			planError = formulaErrorMessage(error, 'Could not check this formula.');
		} finally {
			if (request === planRequest) {
				planLoading = false;
				planStale = false;
			}
		}
	}

	function openApply(formula: Formula) {
		applying = formula;
		serverPlan = null;
		excluded = new Set();
		loraMode = 'replace';
		view = 'apply';
		void fetchPlan(formula);
	}

	let planKey = '';
	let planStale = $state(false);
	let planTimer: ReturnType<typeof setTimeout> | undefined;

	$effect(() => {
		const key = view === 'apply' && applying ? JSON.stringify(tab.formData ?? {}) : '';
		if (key === planKey) return;
		const hadPlan = planKey !== '';
		planKey = key;
		if (!key) {
			planStale = false;
			return;
		}
		if (!hadPlan) return;
		planStale = true;
		const formula = applying;
		clearTimeout(planTimer);
		planTimer = setTimeout(() => {
			if (formula && view === 'apply' && applying?.id === formula.id) void fetchPlan(formula);
		}, 300);
	});

	onDestroy(() => clearTimeout(planTimer));

	function changeLoraMode(mode: LoraMode) {
		if (mode === loraMode || !applying) return;
		loraMode = mode;
		void fetchPlan(applying);
	}

	function toggleChange(field: string) {
		const next = new Set(excluded);
		if (next.has(field)) next.delete(field);
		else next.add(field);
		excluded = next;
	}

	function runApply(keepOpen: boolean) {
		if (!plan || !applying || planStale) return;
		const result = applyPlan(tab.formData ?? {}, plan, selected);
		if (Object.keys(result.changed).length === 0) return;
		tabsStore.updateTab(tab.id, { formData: result.formData });
		formulaApplied.set(tab.id, {
			formulaId: applying.id,
			name: applying.name,
			key: appliedKey(tab),
			changed: result.changed,
			skipped: plan.skips.length,
			snapshot: result.snapshot
		});
		if (keepOpen) {
			backToList();
			return;
		}
		onClose();
		onApplied?.();
	}

	async function handleMenu(formula: Formula, action: MenuAction) {
		if (action === 'rename') {
			renamingId = formula.id;
			renameValue = formula.name;
		} else if (action === 'duplicate') {
			try {
				const response = await api.duplicateFormula(formula.id);
				if (!response.success || !response.data) throw new Error(response.error || 'Could not duplicate.');
				upsertFormula(response.data);
				flashRow(response.data.id);
			} catch (error) {
				listNotice = formulaErrorMessage(error, 'Could not duplicate the formula.');
			}
		} else if (action === 'update') {
			editing = formula;
			saveError = null;
			view = 'save';
		} else {
			const confirmed = await confirmDialog({
				title: 'Delete formula?',
				message: `“${formula.name}” will be deleted. Your sessions and the current form are not affected.`,
				variant: 'danger'
			});
			if (!confirmed) return;
			try {
				const response = await api.deleteFormula(formula.id);
				if (!response.success) throw new Error(response.error || 'Could not delete.');
				removeFormula(formula.preset_id, formula.mode, formula.id);
			} catch (error) {
				listNotice = formulaErrorMessage(error, 'Could not delete the formula.');
			}
		}
	}

	async function commitRename(formula: Formula) {
		if (renamingId !== formula.id) return;
		const name = renameValue.trim();
		renamingId = null;
		if (!name || name === formula.name) return;
		try {
			const response = await api.updateFormula(formula.id, { name });
			if (!response.success || !response.data) throw new Error(response.error || 'Could not rename.');
			upsertFormula(response.data);
		} catch (error) {
			listNotice = formulaErrorMessage(error, 'Could not rename the formula.');
		}
	}

	function savedDate(formula: Formula): string {
		const date = parseServerDate(formula.updated_at);
		return date ? date.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '';
	}

	function focusOnMount(node: HTMLElement) {
		node.focus();
		(node as HTMLInputElement).select?.();
	}
</script>

<DrawerShell label="Formulas" keepOpenKey={KEEP_OPEN_KEY} {onClose} onEscape={handleEscape} bind:searchEl>
	{#snippet header({ keepOpen, setKeepOpen })}
		{#if view === 'save'}
			<DrawerHeader title={editing ? 'Update formula' : 'Save as formula'} closeLabel="Close formulas" onBack={backToList} {onClose} />
		{:else if view === 'apply'}
			<DrawerHeader title="Apply formula" closeLabel="Close formulas" onBack={backToList} {onClose} />
		{:else}
			<DrawerHeader title="Formulas" count={items.length} closeLabel="Close formulas" {keepOpen} onKeepOpenChange={setKeepOpen} {onClose} />
		{/if}
	{/snippet}

	{#snippet children({ keepOpen })}
		{#if view === 'save'}
			<FormulaSaveView
				{states}
				{presetName}
				{modeLabel}
				initialName={editing?.name ?? ''}
				initialSelected={editing ? editing.groups.map((group) => group.id) : untrack(() => preselectedIds(states))}
				{saving}
				error={saveError}
				submitLabel={editing ? 'Update' : 'Save'}
				onSave={runSave}
				onCancel={backToList}
			/>
		{:else if view === 'apply' && applying}
			<FormulaApplyView
				formula={applying}
				{plan}
				loading={planLoading || planStale}
				error={planError}
				{index}
				{groupOrder}
				{presetName}
				{modeLabel}
				{loraMode}
				{selected}
				ready={schemaReady}
				onToggle={toggleChange}
				onLoraMode={changeLoraMode}
				onApply={() => runApply(keepOpen)}
				onCancel={backToList}
			/>
		{:else}
			<DrawerSearch bind:value={query} bind:el={searchEl} placeholder="Search formulas for this preset and mode" label="Search formulas" />

			{#if keepOpen}<FormulaAppliedBar {tab} compact />{/if}

			{#if schemaError}
				<div class="mx-3 mb-2 flex items-center justify-between gap-3 rounded-lg border border-danger/25 bg-danger/10 px-3 py-2" role="alert">
					<span class="min-w-0 text-xs text-danger">{schemaError}</span>
					<Button variant="secondary" size="xs" onclick={() => (schemaAttempt += 1)}>Retry</Button>
				</div>
			{/if}
			{#if listNotice}<p class="mx-3 mb-2 text-xs text-danger" role="alert">{listNotice}</p>{/if}

			<section class="mx-3 mb-2 flex-shrink-0 rounded-lg border border-line-strong bg-surface-2/60 px-3 py-3" aria-label="Current form">
				<div class="flex items-center gap-2">
					<span class="h-2 w-2 flex-shrink-0 rounded-full bg-success-solid" aria-hidden="true"></span>
					<span class="text-md font-semibold text-fg">Current form</span>
					<span class="min-w-0 flex-1 truncate text-right font-mono text-xs text-fg-subtle">{presetName} · {modeLabel}</span>
				</div>
				<div class="mt-0.5 text-xs text-fg-subtle">
					<span class="font-mono tabular-nums">{states.length}</span>
					{states.length === 1 ? 'group' : 'groups'} can be saved ·
					<span class="font-mono tabular-nums">{differing}</span> differ from the defaults
				</div>
				<Button variant="primary" size="sm" class="mt-2.5 w-full" icon="plus" disabled={states.length === 0 || !schemaReady} onclick={startSave}>
					Save as formula
				</Button>
			</section>

			<div class="drawer-scroll min-h-0 flex-1 overflow-y-auto pb-2" data-testid="formula-list-scroll">
				{#if list?.loading && !list.loaded}
					<div class="flex justify-center py-6"><Spinner size="sm" /></div>
				{:else if list?.error && items.length === 0}
					<div class="px-6 py-8 text-center text-sm text-fg-muted">
						<b class="mb-2 block font-semibold text-danger">{list.error}</b>
						<Button variant="secondary" size="sm" onclick={() => loadFormulas(tab.selectedPreset ?? '', tab.selectedMode ?? '')}>Try again</Button>
					</div>
				{:else if filtered.length === 0}
					<div class="px-6 py-8 text-center text-sm text-fg-muted">
						<b class="mb-1 block font-semibold text-fg">{query ? `No formulas match “${query}”` : 'No formulas for this mode yet'}</b>
						{query ? 'Only formulas for this preset and mode are searched.' : 'Save the settings you like and reuse them in any session.'}
					</div>
				{:else}
					<DrawerGroupHead label="Your formulas" count={filtered.length} />
					<ul class="m-0 list-none p-0">
						{#each filtered as formula (formula.id)}
							{#if renamingId === formula.id}
								<li class="mx-2 flex min-h-[52px] items-center gap-2 px-3">
									<input
										use:focusOnMount
										class="input h-9 min-w-0 flex-1 text-sm"
										aria-label={`Rename ${formula.name}`}
										maxlength="80"
										bind:value={renameValue}
										onkeydown={(event) => {
											if (event.key === 'Enter') {
												event.preventDefault();
												commitRename(formula);
											}
										}}
										onblur={() => commitRename(formula)}
									/>
								</li>
							{:else}
								<DrawerRow rowId={formula.id} highlight={savedId === formula.id} onSelect={() => openApply(formula)}>
									{#snippet title()}
										<Tooltip text={formula.name} position="top" delay={400} wrapperClass="flex min-w-0 max-w-full">
											<span class="min-w-0 truncate">{formula.name}</span>
										</Tooltip>
									{/snippet}
									{#snippet meta()}
										<span class="block truncate font-mono text-xs text-fg-subtle">{formula.groups.map((group) => group.label).join(' · ')}</span>
									{/snippet}
									{#snippet actions()}
										<Tooltip text={fullDateTime(formula.updated_at)} position="bottom" delay={300}>
											<span class="flex-shrink-0 px-1 font-mono text-xs tabular-nums text-fg-subtle">{savedDate(formula)}</span>
										</Tooltip>
										<FormulaRowMenu name={formula.name} onAction={(action) => handleMenu(formula, action)} />
									{/snippet}
								</DrawerRow>
							{/if}
						{/each}
					</ul>
				{/if}
			</div>
		{/if}
	{/snippet}
</DrawerShell>
