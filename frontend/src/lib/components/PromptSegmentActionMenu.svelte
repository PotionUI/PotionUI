<script lang="ts">
	import { createEventDispatcher } from 'svelte';

	// The segment action menu, shared by both PromptSegment.svelte variants
	// (content and break rows). Owns no open/position state — the caller
	// supplies `style` (see menuPosition.ts `computeFixedMenuPosition`) and
	// unmounts this component when the menu should close. Anatomy/classes
	// come from the mock's `.segment-menu` (three `.menu-group`s: reorder,
	// edit actions, destructive-adjacent) — visuals live in
	// segment-composer.css, scoped under the ancestor `.segment-composer`.
	export let index: number;
	export let total: number;
	export let isBreakSegment = false;
	export let segmentDisabled = false;
	export let ariaLabel: string;
	export let style = '';
	// Content cards carry a header cluster that already surfaces
	// Disable/Duplicate/Details/Save, so the menu drops them rather than
	// offering the same action twice. Break rows have no such cluster and
	// keep the full set — see segmentFooter.ts for that action list.
	export let footerActionsShown = false;

	const dispatch = createEventDispatcher();
</script>

<div class="floating segment-menu action-menu" role="menu" aria-label={ariaLabel} {style}>
	<div class="menu-group">
		<button type="button" role="menuitem" class="menu-item" disabled={index === 0} on:click={() => dispatch('moveUp')}>
			<svg class="icon"><use href="#i-chevron-up" /></svg>
			<span>Move up</span>
		</button>
		<button type="button" role="menuitem" class="menu-item" disabled={index >= total - 1} on:click={() => dispatch('moveDown')}>
			<svg class="icon"><use href="#i-chevron-down" /></svg>
			<span>Move down</span>
		</button>
	</div>
	<div class="menu-group">
		{#if !footerActionsShown}
			<button type="button" role="menuitem" class="menu-item" on:click={() => dispatch('editDetails')}>
				<svg class="icon"><use href="#i-pencil" /></svg>
				<span>Edit details</span>
			</button>
			<button type="button" role="menuitem" class="menu-item" on:click={() => dispatch('saveAsSegment')}>
				<svg class="icon"><use href="#i-save" /></svg>
				<span>Save as Segment</span>
			</button>
		{/if}
		<button type="button" role="menuitem" class="menu-item" on:click={() => dispatch('replaceFromSaved')}>
			<svg class="icon"><use href="#i-library" /></svg>
			<span>Replace from saved</span>
		</button>
		<button type="button" role="menuitem" class="menu-item" on:click={() => dispatch('toggleBreak')}>
			<svg class="icon"><use href="#i-break" /></svg>
			<span>{isBreakSegment ? 'Convert to content' : 'Convert to break'}</span>
		</button>
	</div>
	<div class="menu-group">
		{#if !footerActionsShown}
			<button type="button" role="menuitem" class="menu-item" on:click={() => dispatch('duplicate')}>
				<svg class="icon"><use href="#i-copy" /></svg>
				<span>Duplicate</span>
			</button>
			<button type="button" role="menuitem" class="menu-item" on:click={() => dispatch('toggleDisabled')}>
				<svg class="icon"><use href={segmentDisabled ? '#i-eye' : '#i-eye-off'} /></svg>
				<span>{segmentDisabled ? 'Enable' : 'Disable'}</span>
			</button>
		{/if}
		<button
			type="button"
			role="menuitem"
			class="menu-item danger"
			disabled={total <= 1}
			title={total <= 1 ? 'Every prompt needs at least one segment' : undefined}
			on:click={() => dispatch('remove')}
		>
			<svg class="icon"><use href="#i-trash" /></svg>
			<span>Delete</span>
		</button>
	</div>
</div>

<style>
	/* Position/elevation only — chrome, groups and item styling all come from
	   the ported `.segment-menu`/`.menu-group`/`.menu-item` rules. */
	.action-menu {
		position: fixed;
		z-index: 30;
	}

	.action-menu .menu-item:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}
</style>
