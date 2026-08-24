<!--
	Mono uppercase title + hairline, no indent. Sits between header (big type)
	and group/accordion (railed, nested containers).
	See src/features/fields/section.py.

	A section with `children` is a real container: its title row is a fold
	toggle and it renders its own children below. A section with no children
	is a plain divider - no chevron, no click target, not a `<button>`.
-->
<script lang="ts">
	import { getContext, setContext } from 'svelte';
	import { readable } from 'svelte/store';
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';
	import FieldChildren from './FieldChildren.svelte';
	import { resolveSectionCollapsed } from './sectionState';
	import {
		SECTION_COLLAPSED_CONTEXT_KEY,
		type SectionCollapsedContext
	} from '$lib/form/sectionCollapsedContext';
	import { ROW_INSET_CONTEXT_KEY, SECTION_WELL_INSET, accumulatedInset } from './rowInset';

	export let config: any = {};
	export let value: any = undefined;
	export let onChange: (fieldName: string, value: any) => void = () => {};
	export let onOriginChange: ((fieldName: string, origin: unknown) => void) | undefined = undefined;
	export let onMaskChange: ((fieldName: string, maskPath: string | undefined) => void) | undefined = undefined;
	// This section's structural identity (see FieldChildren.svelte), used to
	// key its persisted fold state. `undefined` when no ancestor supplied one
	// (shouldn't happen in practice - DynamicForm always seeds the root path).
	export let fieldPath: string | undefined = undefined;

	$: title = config.title || config.label || '';
	$: badge = config.badge || '';
	$: tooltip = config.tooltip || '';
	$: experimental = config.experimental === true;
	$: hasChildren = Array.isArray(config.children) && config.children.length > 0;

	// Optional: unset outside the generate page, or wherever DynamicForm was
	// given no `sectionCollapsedContext` - see sectionCollapsedContext.ts.
	const persistedFold = getContext<SectionCollapsedContext | undefined>(
		SECTION_COLLAPSED_CONTEXT_KEY
	);

	// Set unconditionally at init (Svelte requires setContext to run during
	// component initialization, not inside a conditional/reactive block) even
	// though the well only renders when `hasChildren` - a child-less section
	// has no descendants to read it back. Nested sections accumulate: each
	// reads its parent's inset and adds its own well on top.
	setContext(
		ROW_INSET_CONTEXT_KEY,
		accumulatedInset(getContext<number | undefined>(ROW_INSET_CONTEXT_KEY), SECTION_WELL_INSET)
	);

	// Subscribed, not read once: `collapsed` has to re-derive when a toggle (or a
	// session restore) writes the map, otherwise the fold only shows up after a
	// page reload.
	const folded = persistedFold?.folded ?? readable<Record<string, boolean>>({});

	// Fallback for when persistedFold/fieldPath is unavailable - behaves exactly
	// like the pre-persistence version of this component.
	let localCollapsed = config.collapsed === true;

	$: persists = !!persistedFold && !!fieldPath;
	$: collapsed = persists
		? resolveSectionCollapsed(config, $folded[fieldPath as string])
		: localCollapsed;

	function toggleCollapsed() {
		const next = !collapsed;
		if (persistedFold && fieldPath) {
			persistedFold.set(fieldPath, next);
		} else {
			localCollapsed = next;
		}
	}
</script>

{#snippet titleRow()}
	<span class="font-mono text-xs font-semibold uppercase tracking-[0.08em] text-fg">
		{title}
	</span>
	{#if tooltip}
		<Tooltip text={tooltip} position="top">
			<span class="inline-flex cursor-help items-center text-fg-subtle">
				<Icon name="info" className="h-3.5 w-3.5" />
			</span>
		</Tooltip>
	{/if}
	<span class="h-px flex-1 bg-line"></span>
	{#if badge}
		<span class="font-mono text-2xs text-fg-subtle">{badge}</span>
	{/if}
	{#if experimental}
		<Tooltip text="This section is experimental — behavior and results may change." position="top">
			<span
				class="inline-flex items-center rounded border border-warning/25 bg-warning/10 px-1.5 py-0.5 text-2xs font-medium text-warning"
			>
				Experimental
			</span>
		</Tooltip>
	{/if}
	{#if hasChildren}
		<svg
			class="h-3 w-3 shrink-0 text-fg-subtle transition-transform {collapsed ? '' : 'rotate-180'}"
			fill="none"
			viewBox="0 0 24 24"
			stroke="currentColor"
		>
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width={2} d="M19 9l-7 7-7-7" />
		</svg>
	{/if}
{/snippet}

{#if hasChildren}
	<button
		type="button"
		class="-mx-2 flex items-center gap-2 rounded px-2 py-1.5 text-left transition-colors hover:bg-surface-2"
		style="width: calc(100% + 1rem)"
		on:click={toggleCollapsed}
		aria-expanded={!collapsed}
	>
		{@render titleRow()}
	</button>
	<!-- Content stays mounted while folded (max-height/opacity, not {#if}), matching AccordionField -->
	<div
		class="{collapsed
			? 'overflow-hidden'
			: 'overflow-visible'} transition-[max-height,opacity] duration-300 ease-out motion-reduce:duration-0"
		style="max-height: {collapsed ? '0px' : '4000px'}; opacity: {collapsed ? 0 : 1};"
	>
		<!-- Tint lives on an inner wrapper: padding on the animated element above
		     would survive the fold as an empty tinted bar. -->
		<div class="mt-1 space-y-2 rounded-lg bg-surface-2/40 p-3">
			<FieldChildren children={config.children} {value} {onChange} {onOriginChange} {onMaskChange} location="section" {fieldPath} />
		</div>
	</div>
{:else}
	<div class="flex w-full items-center gap-2 py-1.5">
		{@render titleRow()}
	</div>
{/if}
