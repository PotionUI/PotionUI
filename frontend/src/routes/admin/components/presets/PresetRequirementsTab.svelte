<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '$lib/services/api/index';
	import { logger } from '$lib/utils/logger';
	import { timeAgo } from '$lib/utils/relativeTime';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge, Button, EmptyState, IconButton, Spinner } from '$lib/components/ui';
	import type { RequirementResultInfo } from '$lib/types/api';

	export let presetId: string;

	type SectionName = 'System' | 'Python' | 'Models' | 'Backend' | 'Other';
	type Severity = 'ok' | 'warning' | 'danger';

	// Requirement `type:` -> the section it groups under. Anything not listed
	// here (a plugin-registered type this mapping doesn't know about yet)
	// falls into "Other" rather than being dropped.
	const SECTION_BY_TYPE: Record<string, SectionName> = {
		binary: 'System',
		platform: 'System',
		vram_min_gb: 'System',
		python_package: 'Python',
		model: 'Models',
		comfyui_model: 'Models',
		comfyui_node: 'Backend'
	};
	const SECTION_ORDER: SectionName[] = ['System', 'Python', 'Models', 'Backend', 'Other'];

	const SEVERITY_DOT: Record<Severity, string> = {
		ok: 'bg-success',
		warning: 'bg-warning',
		danger: 'bg-danger'
	};
	const SEVERITY_HINT: Record<Severity, string> = {
		ok: '',
		warning: 'text-warning',
		danger: 'text-danger'
	};

	// A `missing` optional entry is a soft warning, not a hard blocker - only a
	// required (non-optional) miss is danger. `unknown` is always a warning
	// (never "missing" - see RequirementStatus's docstring).
	function severity(item: RequirementResultInfo): Severity {
		if (item.status === 'ok') return 'ok';
		if (item.status === 'unknown') return 'warning';
		return item.optional ? 'warning' : 'danger';
	}

	let results: RequirementResultInfo[] = [];
	let summary = { ok: 0, missing: 0, unknown: 0 };
	let checkedAt: number | null = null;
	let loading = true;
	let refreshing = false;
	let loadError = '';
	// Sections a user has manually expanded/collapsed, overriding the default
	// (a section with any non-ok item starts expanded; an all-ok one starts
	// collapsed to its one-line header).
	let manualExpand: Partial<Record<SectionName, boolean>> = {};

	$: total = summary.ok + summary.missing + summary.unknown;
	$: verdict =
		summary.missing > 0
			? {
					tone: 'danger' as const,
					label: "Can't run here",
					text: `This preset can't run here: ${summary.missing} missing.`
				}
			: summary.unknown > 0
				? {
						tone: 'warning' as const,
						label: `${summary.unknown} unknown`,
						text: `Can't confirm this preset can run here — ${summary.unknown} requirement${summary.unknown === 1 ? '' : 's'} unchecked.`
					}
				: {
						tone: 'success' as const,
						label: 'Ready',
						text: 'This preset is ready to run.'
					};

	$: sections = SECTION_ORDER.map((name) => {
		const items = results.filter((result) => (SECTION_BY_TYPE[result.type || ''] || 'Other') === name);
		const ok = items.filter((item) => severity(item) === 'ok').length;
		const danger = items.filter((item) => severity(item) === 'danger').length;
		// "unknown" (couldn't be checked) is kept apart from "optional missing" -
		// they're both warning-severity, but only the former earns the "? N"
		// badge treatment below.
		const unknown = items.filter((item) => item.status === 'unknown').length;
		const optionalMissing = items.filter((item) => item.status === 'missing' && item.optional).length;
		return {
			name,
			items,
			ok,
			danger,
			unknown,
			optionalMissing,
			hasProblem: danger > 0 || unknown > 0 || optionalMissing > 0
		};
	}).filter((section) => section.items.length > 0);

	function isExpanded(section: { name: SectionName; hasProblem: boolean }): boolean {
		return manualExpand[section.name] ?? section.hasProblem;
	}

	function toggleSection(section: { name: SectionName; hasProblem: boolean }) {
		manualExpand = { ...manualExpand, [section.name]: !isExpanded(section) };
	}

	onMount(() => {
		load();
	});

	async function load(refresh = false) {
		if (refresh) refreshing = true;
		else loading = true;
		loadError = '';
		try {
			const response = await api.getPresetRequirements(presetId, refresh);
			if (!response.success || !response.data) {
				throw new Error(response.message || 'Could not check requirements');
			}
			results = response.data.results || [];
			summary = response.data.summary || { ok: 0, missing: 0, unknown: 0 };
			checkedAt = response.data.checked_at ?? null;
			manualExpand = {};
		} catch (error) {
			logger.error('Failed to check preset requirements:', error);
			loadError = error instanceof Error ? error.message : 'Could not check requirements';
		} finally {
			loading = false;
			refreshing = false;
		}
	}

	function runAction(action: NonNullable<RequirementResultInfo['action']>) {
		if (action.kind === 'open_downloader') {
			void goto('/admin?tab=downloads');
		} else if (action.kind === 'open_backends') {
			void goto('/admin?tab=backends');
		} else if (action.kind === 'open_url' && typeof action.payload?.url === 'string') {
			window.open(action.payload.url, '_blank', 'noopener');
		}
	}

	function actionLabel(kind: string): string {
		if (kind === 'open_downloader') return 'Open Downloader';
		if (kind === 'open_backends') return 'Open Backends';
		return 'Open link';
	}

	function actionIcon(kind: string): string {
		if (kind === 'open_downloader') return 'download';
		if (kind === 'open_backends') return 'server';
		return 'external-link';
	}
</script>

<div>
	{#if loading}
		<div class="rounded-lg border border-line bg-surface-1 py-10 flex flex-col items-center justify-center">
			<Spinner size="md" />
			<p class="text-sm text-fg-muted mt-3">Checking requirements…</p>
		</div>
	{:else if loadError}
		<EmptyState title="Requirements check unavailable" description={loadError} icon="warning" compact>
			{#snippet actions()}<Button variant="secondary" size="sm" icon="refresh" onclick={() => load()}>Try again</Button>{/snippet}
		</EmptyState>
	{:else if total === 0}
		<EmptyState title="No requirements declared" description="This preset declares no requirements." icon="check" compact />
	{:else}
		<div
			class="rounded-lg border border-line-strong bg-canvas p-4 sm:p-5 mb-4"
			data-testid="requirements-hero"
		>
			<div class="flex items-end gap-3.5">
				<div class="font-mono tabular-nums text-2xl font-semibold text-fg leading-none">
					{summary.ok}<span class="text-sm font-medium text-fg-subtle ml-1">/ {total} satisfied</span>
				</div>
				<div class="flex-1"></div>
				<Badge variant={verdict.tone} size="md">{verdict.label}</Badge>
			</div>

			<div class="flex gap-[3px] mt-3" role="img" aria-label="{summary.ok} of {total} requirements satisfied">
				{#each results as result}
					<div
						class="flex-1 h-1.5 rounded-sm {result.status === 'ok'
							? 'bg-success'
							: result.status === 'missing'
								? 'bg-transparent border border-danger'
								: 'bg-transparent border border-dashed border-warning'}"
					></div>
				{/each}
			</div>

			<div class="flex items-center gap-2.5 mt-3">
				<p
					class="text-sm font-semibold flex-1 {verdict.tone === 'danger'
						? 'text-danger'
						: verdict.tone === 'warning'
							? 'text-warning'
							: 'text-success'}"
				>
					{verdict.text}
				</p>
				<span class="font-mono text-2xs text-fg-subtle whitespace-nowrap">
					{checkedAt ? `checked ${timeAgo(new Date(checkedAt * 1000).toISOString())}` : ''}
				</span>
				<Tooltip text="Re-check requirements">
					<IconButton
						icon="refresh"
						label="Re-check requirements"
						size="sm"
						disabled={refreshing}
						class={refreshing ? 'animate-spin' : ''}
						onclick={() => load(true)}
					/>
				</Tooltip>
			</div>
		</div>

		<div class="space-y-2.5">
			{#each sections as section (section.name)}
				<div class="rounded-lg border border-line bg-surface-1 overflow-hidden">
					<button
						type="button"
						class="w-full flex items-center gap-2 px-3.5 py-2.5 text-left {isExpanded(section)
							? 'border-b border-line bg-canvas'
							: ''}"
						onclick={() => toggleSection(section)}
						aria-expanded={isExpanded(section)}
					>
						<Icon
							name="chevron-right"
							className="w-3.5 h-3.5 text-fg-subtle transition-transform {isExpanded(section) ? 'rotate-90' : ''}"
						/>
						<span class="text-sm font-semibold text-fg">{section.name}</span>
						<span class="flex-1"></span>
						{#if section.danger > 0}
							<Badge variant="danger" size="sm">{section.ok}/{section.items.length}</Badge>
						{:else if section.unknown > 0}
							<Badge variant="warning" size="sm">? {section.unknown}</Badge>
						{:else if section.optionalMissing > 0}
							<Badge variant="warning" size="sm">{section.ok}/{section.items.length}</Badge>
						{:else}
							<Badge variant="success" size="sm">{section.ok}/{section.items.length}</Badge>
						{/if}
					</button>

					{#if isExpanded(section)}
						<div class="px-3.5 py-1">
							{#each section.items as item, index}
								{@const itemSeverity = severity(item)}
								<div class="flex items-start gap-2.5 py-2.5 {index > 0 ? 'border-t border-line' : ''}">
									<span class="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0 {SEVERITY_DOT[itemSeverity]}"></span>
									<div class="min-w-0 flex-1">
										<div class="flex items-baseline gap-2 flex-wrap">
											<span class="font-mono text-sm font-semibold text-fg">{item.name || item.type || 'requirement'}</span>
											<span class="text-xs text-fg-muted">{item.detail}</span>
											{#if item.optional}<Badge variant="neutral" size="sm">optional</Badge>{/if}
										</div>
										{#if item.hint && item.status !== 'ok'}
											<p class="text-xs mt-1 {SEVERITY_HINT[itemSeverity]}">→ {item.hint}</p>
										{/if}
									</div>
									{#if item.action}
										<Button variant="secondary" size="sm" icon={actionIcon(item.action.kind)} onclick={() => runAction(item.action!)}>
											{actionLabel(item.action.kind)}
										</Button>
									{/if}
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>
