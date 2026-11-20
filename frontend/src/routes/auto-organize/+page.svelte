<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import { Alert, Button, EmptyState, LoadErrorState, SegmentedControl, Spinner } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import { HeaderDropdown } from '$lib/components/page-header';
	import LibraryShell from '$lib/components/library/LibraryShell.svelte';
	import LibraryListHeader from '$lib/components/library/LibraryListHeader.svelte';
	import type { LibrarySectionMeta } from '$lib/components/library/librarySection';
	import ActivityList from '$lib/components/organize/ActivityList.svelte';
	import RuleEditor from '$lib/components/organize/RuleEditor.svelte';
	import RuleList from '$lib/components/organize/RuleList.svelte';
	import TemplateGrid from '$lib/components/organize/TemplateGrid.svelte';
	import { api } from '$lib/services/api';
	import { confirmDialog } from '$lib/stores/confirm';
	import { toasts } from '$lib/stores/toast';
	import { draftFromParts, draftFromRule, emptyDraft, effectiveKind, type RuleDraft, type ValueLabels } from '$lib/organize/draft';
	import { parseOrganizeError } from '$lib/organize/errors';
	import { startRuleFrom, takePendingRule } from '$lib/organize/handoff';
	import { resolveModelNames } from '$lib/organize/labels';
	import { attentionCount } from '$lib/organize/status';
	import { ORGANIZE_SUBJECTS, organizeHref, slugFromSubject, subjectFromSlug, type OrganizeSlug } from '$lib/organize/subjects';
	import { templatesFor } from '$lib/organize/templates';
	import { writeLastSubject } from '$lib/organize/lastSubject';
	import { subjectNoun } from '$lib/organize/sentence';
	import type { OrganizeCatalog, OrganizeRule, OrganizeSubject, OrganizeSummary } from '$lib/types/organize';

	const sections: readonly LibrarySectionMeta<OrganizeSlug>[] = ORGANIZE_SUBJECTS.map((s) => ({
		id: s.slug,
		label: s.label,
		icon: s.icon
	}));

	let catalog = $state<OrganizeCatalog | null>(null);
	let summary = $state<OrganizeSummary | null>(null);
	let rules = $state<OrganizeRule[]>([]);
	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let busyId = $state<string | null>(null);
	let query = $state('');
	let modelNames = $state<Record<string, string>>({});
	let editor = $state<{ key: number; draft: RuleDraft } | null>(null);
	let activityRefresh = $state(0);
	let editorCounter = 0;
	let resolvingRule = $state(false);

	const subjectParam = $derived($page.url.searchParams.get('subject'));
	const subject = $derived<OrganizeSubject>(subjectFromSlug(subjectParam));
	const slug = $derived(slugFromSubject(subject));
	const view = $derived($page.url.searchParams.get('view') === 'activity' ? 'activity' : 'rules');
	const ruleParam = $derived($page.url.searchParams.get('rule'));
	const noun = $derived(subjectNoun(subject));

	$effect(() => {
		writeLastSubject(slug);
	});

	type RuleSort = 'manual' | 'name' | 'last_run' | 'created';
	const SORT_OPTIONS: Array<{ value: RuleSort; label: string }> = [
		{ value: 'manual', label: 'Manual' },
		{ value: 'name', label: 'Name' },
		{ value: 'last_run', label: 'Last run' },
		{ value: 'created', label: 'Created' }
	];
	const SORT_DEFAULT_DIR: Record<RuleSort, 'asc' | 'desc'> = {
		manual: 'asc',
		name: 'asc',
		last_run: 'desc',
		created: 'desc'
	};
	const STATUS_LABELS: Record<string, string> = {
		active: 'Active',
		off: 'Off',
		paused: 'Paused',
		needs_attention: 'Needs attention'
	};

	let statusFilter = $state('');
	let triggerFilter = $state('');
	let sortBy = $state<RuleSort>('manual');
	let sortDir = $state<'asc' | 'desc'>('asc');

	const triggerOptions = $derived([...new Set(rules.map((rule) => rule.trigger))].sort());
	const statusOptions = [{ value: '', label: 'Any' }, ...Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label }))];
	const triggerSelectOptions = $derived([
		{ value: '', label: 'Any' },
		...triggerOptions.map((value) => ({ value, label: humanize(value) }))
	]);
	const humanize = (value: string) => {
		const text = value.replace(/_/g, ' ');
		return text.charAt(0).toUpperCase() + text.slice(1);
	};

	const filtersActive = $derived(!!statusFilter || !!triggerFilter);
	const reorderable = $derived(sortBy === 'manual' && !filtersActive && !query.trim());

	const visibleRules = $derived.by(() => {
		const q = query.trim().toLowerCase();
		const matched = rules.filter(
			(r) =>
				(!q || r.name.toLowerCase().includes(q)) &&
				(!statusFilter || r.status === statusFilter) &&
				(!triggerFilter || r.trigger === triggerFilter)
		);
		if (sortBy === 'manual') return matched;
		const sign = sortDir === 'desc' ? -1 : 1;
		const stamp = (value: string | null) => (value ? Date.parse(value) || 0 : 0);
		if (sortBy === 'name') return [...matched].sort((a, b) => sign * a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
		if (sortBy === 'last_run') return [...matched].sort((a, b) => sign * (stamp(a.last_run_at) - stamp(b.last_run_at)));
		return [...matched].sort((a, b) => sign * (stamp(a.created_at) - stamp(b.created_at)));
	});

	const headerChips = $derived([
		...(statusFilter ? [{ key: 'status', label: `Status: ${STATUS_LABELS[statusFilter] ?? statusFilter}` }] : []),
		...(triggerFilter ? [{ key: 'trigger', label: `Trigger: ${humanize(triggerFilter)}` }] : [])
	]);

	function removeChip(key: string) {
		if (key === 'status') statusFilter = '';
		else if (key === 'trigger') triggerFilter = '';
	}

	function clearFilters() {
		statusFilter = '';
		triggerFilter = '';
	}
	const labels = $derived<ValueLabels>({ models: modelNames, options: {} });
	const collectionNames = $derived.by(() => {
		const out: Record<string, string> = {};
		for (const rule of rules) for (const c of rule.targets.collections) out[c.id] = c.name;
		return out;
	});
	const sectionCounts = $derived.by(() => {
		const out: Partial<Record<OrganizeSlug, number>> = {};
		if (summary) for (const s of ORGANIZE_SUBJECTS) out[s.slug] = summary.subjects[s.subject]?.total ?? 0;
		return out;
	});
	const templates = $derived(templatesFor(subject, catalog));
	const attention = $derived(attentionCount(rules));

	function url(extra: Record<string, string | null | undefined> = {}, subj: OrganizeSubject = subject): string {
		return organizeHref(subj, extra);
	}

	function selectSection(next: OrganizeSlug) {
		query = '';
		void goto(url({}, subjectFromSlug(next)));
	}

	function selectView(next: string) {
		void goto(url({ view: next === 'activity' ? 'activity' : null }));
	}

	function backToList() {
		editor = null;
		void goto(url({ view: view === 'activity' ? 'activity' : null }));
	}

	async function loadRules() {
		const [rulesResult, summaryResult] = await Promise.allSettled([
			api.listOrganizeRules(subject),
			api.getOrganizeSummary()
		]);
		if (rulesResult.status === 'fulfilled' && rulesResult.value.success) {
			rules = rulesResult.value.data ?? [];
			loadError = null;
		} else {
			loadError =
				rulesResult.status === 'fulfilled'
					? rulesResult.value.message || 'The rules could not be loaded.'
					: parseOrganizeError(rulesResult.reason, 'The rules could not be loaded.').message;
		}
		if (summaryResult.status === 'fulfilled' && summaryResult.value.success) summary = summaryResult.value.data ?? null;
		await resolveLabels();
	}

	async function resolveLabels() {
		if (!catalog) return;
		const ids: string[] = [];
		for (const rule of rules) {
			for (const cond of rule.conditions) {
				const spec = catalog.facts.find((f) => f.key === cond.fact);
				if (effectiveKind(spec) !== 'model_ref') continue;
				if (Array.isArray(cond.value)) ids.push(...(cond.value as string[]));
				else if (typeof cond.value === 'string') ids.push(cond.value);
			}
		}
		if (ids.length > 0) modelNames = { ...modelNames, ...(await resolveModelNames(ids)) };
	}

	async function loadAll() {
		loading = true;
		if (!catalog) {
			try {
				const response = await api.getOrganizeCatalog();
				if (response.success && response.data) catalog = response.data;
			} catch (err) {
				loadError = parseOrganizeError(err, 'Auto-organize could not be loaded.').message;
			}
		}
		await loadRules();
		loading = false;
	}

	let loadedSubject: OrganizeSubject | null = null;
	$effect(() => {
		const current = subject;
		if (loadedSubject === current) return;
		loadedSubject = current;
		void loadAll();
	});

	$effect(() => {
		const id = ruleParam;
		const cat = catalog;
		if (!id) {
			editor = null;
			return;
		}
		if (!cat) return;
		if (id === 'new') {
			if (editor && editor.draft.id === null) return;
			const pending = takePendingRule();
			const useSubject = pending?.subject ?? subject;
			const draft = pending ? draftFromParts(useSubject, pending) : emptyDraft(useSubject);
			editorCounter += 1;
			editor = { key: editorCounter, draft };
			if (useSubject !== subject) void goto(url({ rule: 'new' }, useSubject), { replaceState: true });
			return;
		}
		if (editor?.draft.id === id) return;
		void openExisting(id);
	});

	async function openExisting(id: string) {
		resolvingRule = true;
		try {
			const known = rules.find((r) => r.id === id);
			const rule = known ?? (await api.getOrganizeRule(id)).data;
			if (!rule) {
				toasts.error('That rule could not be found.');
				void goto(url(), { replaceState: true });
				return;
			}
			if (rule.subject !== subject) {
				void goto(url({ rule: id }, rule.subject), { replaceState: true });
				return;
			}
			editorCounter += 1;
			editor = { key: editorCounter, draft: draftFromRule(rule) };
		} catch {
			toasts.error('That rule could not be found.');
			void goto(url(), { replaceState: true });
		} finally {
			resolvingRule = false;
		}
	}

	function upsert(rule: OrganizeRule) {
		const exists = rules.some((r) => r.id === rule.id);
		rules = exists ? rules.map((r) => (r.id === rule.id ? rule : r)) : [...rules, rule];
		void resolveLabels();
		void api.getOrganizeSummary().then((r) => {
			if (r.success && r.data) summary = r.data;
		});
		activityRefresh += 1;
	}

	async function handleToggle(rule: OrganizeRule, next: boolean) {
		busyId = rule.id;
		try {
			const response = await api.patchOrganizeRule(rule.id, { enabled: next });
			if (response.success && response.data) upsert(response.data);
			else toasts.error(response.message || 'The rule could not be changed.');
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The rule could not be changed.').message);
		} finally {
			busyId = null;
		}
	}

	async function handleToggleStop(rule: OrganizeRule) {
		try {
			const response = await api.patchOrganizeRule(rule.id, { stop_after: !rule.stop_after });
			if (response.success && response.data) upsert(response.data);
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The rule could not be changed.').message);
		}
	}

	async function handleDuplicate(rule: OrganizeRule) {
		try {
			const response = await api.duplicateOrganizeRule(rule.id);
			if (response.success && response.data) {
				upsert(response.data);
				toasts.success(`Copied "${rule.name}". The copy is switched off.`);
			}
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The rule could not be copied.').message);
		}
	}

	async function handleDelete(rule: OrganizeRule) {
		const ok = await confirmDialog({
			title: `Delete "${rule.name}"?`,
			message: 'Items it already filed stay where they are, and its activity stays in the list.',
			variant: 'danger'
		});
		if (!ok) return;
		try {
			const response = await api.deleteOrganizeRule(rule.id);
			if (response.success) {
				rules = rules.filter((r) => r.id !== rule.id);
				void api.getOrganizeSummary().then((r) => {
					if (r.success && r.data) summary = r.data;
				});
			}
		} catch (err) {
			toasts.error(parseOrganizeError(err, 'The rule could not be deleted.').message);
		}
	}

	async function handleReorder(ids: string[]) {
		if (query.trim()) return;
		const previous = rules;
		rules = ids.map((id) => previous.find((r) => r.id === id)!).filter(Boolean);
		try {
			const response = await api.reorderOrganizeRules(subject, ids);
			if (response.success && response.data) rules = response.data;
			else rules = previous;
		} catch (err) {
			rules = previous;
			toasts.error(parseOrganizeError(err, 'The order could not be saved.').message);
		}
	}

	function newRule() {
		void goto(url({ rule: 'new' }));
	}
</script>

<svelte:head>
	<title>Auto-organize · PotionUI</title>
</svelte:head>

<LibraryShell
	title="Auto-organize"
	persistKey="auto-organize"
	{sections}
	section={slug}
	onSelectSection={selectSection}
	{sectionCounts}
	count={rules.length}
	detailOpen={!!editor || resolvingRule}
	titleLabel="Auto-organize"
>
	{#snippet header(sectionSelect)}
		<LibraryListHeader
			title="Auto-organize"
			count={visibleRules.length}
			total={rules.length}
			unit="rules"
			unitOne="rule"
			chips={view === 'rules' ? headerChips : []}
			onRemoveChip={removeChip}
			onClearFilters={clearFilters}
			q={query}
			onQueryChange={(value) => (query = value)}
			searchPlaceholder="Search rules by name…"
			showSearch={view === 'rules'}
			sortOptions={view === 'rules' ? SORT_OPTIONS : []}
			sortBy={sortBy}
			sortDir={sortDir}
			sortDirDisabled={sortBy === 'manual'}
			onSortChange={(value) => {
				sortBy = value as RuleSort;
				sortDir = SORT_DEFAULT_DIR[value as RuleSort];
			}}
			onSortDirChange={(dir) => (sortDir = dir)}
			onRefresh={view === 'rules' ? loadRules : undefined}
			primaryLabel="New rule"
			onPrimary={newRule}
			{sectionSelect}
		>
			{#snippet popover(close)}
				<div class="w-72 max-w-[90vw] rounded-xl border border-line-strong bg-surface-3 p-3.5 shadow-floating" role="dialog" aria-label="Filters">
					{#if headerChips.length > 0}
						<div class="mb-3 flex items-center">
							<span class="text-sm uppercase tracking-[0.07em] text-fg-subtle">Active</span>
							<button type="button" class="ml-auto text-sm text-fg-subtle hover:text-fg" onclick={clearFilters}>Clear all</button>
						</div>
						<div class="mb-3 flex flex-wrap gap-1.5 border-b border-line pb-3">
							{#each headerChips as chip (chip.key)}
								<span class="inline-flex h-6 max-w-full items-center gap-1 rounded border border-signal/25 bg-signal/10 pl-2 pr-1 text-sm text-signal">
									<span class="truncate">{chip.label}</span>
									<button
										type="button"
										class="inline-flex h-4 w-4 flex-none items-center justify-center rounded-sm hover:bg-signal/10"
										aria-label="Remove {chip.label}"
										onclick={() => removeChip(chip.key)}
									>
										<Icon name="close" className="h-3 w-3" />
									</button>
								</span>
							{/each}
						</div>
					{/if}
					<div class="grid grid-cols-2 gap-3">
						<div class="flex flex-col gap-1.5">
							<span class="text-sm font-medium text-fg-muted">Status</span>
							<HeaderDropdown block ariaLabel="Status" value={statusFilter} options={statusOptions} onchange={(value) => (statusFilter = value)} />
						</div>
						<div class="flex flex-col gap-1.5">
							<span class="text-sm font-medium text-fg-muted">Trigger</span>
							<HeaderDropdown block ariaLabel="Trigger" value={triggerFilter} options={triggerSelectOptions} onchange={(value) => (triggerFilter = value)} />
						</div>
					</div>
					<div class="mt-3.5 flex items-center justify-end border-t border-line pt-3">
						<Button size="xs" variant="secondary" onclick={close}>Done</Button>
					</div>
				</div>
			{/snippet}
		</LibraryListHeader>
	{/snippet}

	{#if editor && catalog}
		{#key editor.key}
			<RuleEditor
				initial={editor.draft}
				{catalog}
				onBack={backToList}
				onSaved={upsert}
				onViewActivity={() => {
					editor = null;
					void goto(url({ view: 'activity' }));
				}}
			/>
		{/key}
	{:else if resolvingRule}
		<div class="flex h-40 items-center justify-center"><Spinner size="lg" /></div>
	{:else}
		<div class="space-y-4 p-4 sm:p-5">
			{#if summary?.paused_by_admin}
				<Alert variant="warning" icon>
					Auto-organize is paused by an administrator. Your rules are kept and start again when it is switched back on.
				</Alert>
			{/if}
			{#if attention > 0}
				<Alert variant="warning" icon>
					{attention === 1 ? 'One rule was paused or needs a look.' : `${attention} rules were paused or need a look.`}
					Open them below to see why.
				</Alert>
			{/if}

			<SegmentedControl
				ariaLabel="Auto-organize view"
				selected={view}
				items={[
					{ id: 'rules', label: 'Rules', count: rules.length || undefined },
					{ id: 'activity', label: 'Activity' }
				]}
				onSelect={selectView}
			/>

			{#if view === 'activity'}
				<ActivityList {subject} refreshKey={activityRefresh} />
			{:else if loading && rules.length === 0}
				<div class="flex h-40 items-center justify-center"><Spinner size="lg" /></div>
			{:else if loadError && rules.length === 0}
				<LoadErrorState message={loadError} onRetry={loadAll} retrying={loading} />
			{:else}
				<Alert variant="info" icon="wand" title="How rules work" live="off">
					Rules file your new {noun.plural} into collections{subject === 'model' ? '' : ' and tags'} for you. They only add, they never remove anything, and every run can be undone from Activity.
				</Alert>
				{#if rules.length === 0}
					<EmptyState
						icon="wand"
						title="No rules yet"
						description="A rule watches for new {noun.plural} that match what you describe and files them for you."
						compact
					>
						{#snippet actions()}
							<Button size="sm" variant="secondary" icon="plus" onclick={newRule}>New rule</Button>
						{/snippet}
					</EmptyState>
				{:else if visibleRules.length === 0}
					<EmptyState icon="search" title="No matching rules" description="Try a different search term." compact />
				{:else}
					<RuleList
						rules={visibleRules}
						{catalog}
						{labels}
						{collectionNames}
						{busyId}
						onOpen={(rule) => goto(url({ rule: rule.id }))}
						onToggle={handleToggle}
						onToggleStop={handleToggleStop}
						onDuplicate={handleDuplicate}
						onDelete={handleDelete}
						onReorder={handleReorder}
						{reorderable}
					/>
				{/if}

				{#if templates.length > 0}
					<TemplateGrid {templates} onUse={(template) => startRuleFrom(template.rule)} />
				{/if}
			{/if}
		</div>
	{/if}
</LibraryShell>
