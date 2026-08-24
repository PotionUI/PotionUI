<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import Icon from './Icon.svelte';

	// The segment action menu, shared by both PromptSegment.svelte variants
	// (content and break rows). Owns no open/position state — the caller
	// supplies `style` (see menuPosition.ts `computeFixedMenuPosition`) and
	// unmounts this component when the menu should close.
	export let index: number;
	export let total: number;
	export let isBreakSegment = false;
	export let segmentDisabled = false;
	export let ariaLabel: string;
	export let style = '';
	// Content cards carry a footer strip that already surfaces
	// Disable/Duplicate/Details/Save, so the menu drops them rather than
	// offering the same action twice. Break rows have no footer and
	// keep the full set — see segmentFooter.ts for that action list.
	export let footerActionsShown = false;

	const dispatch = createEventDispatcher();
</script>

<div class="action-menu" role="menu" aria-label={ariaLabel} {style}>
	<button type="button" role="menuitem" disabled={index === 0} on:click={() => dispatch('moveUp')}>
		<Icon name="chevron-up" className="h-4 w-4" />
		<span>Move up</span>
	</button>
	<button type="button" role="menuitem" disabled={index >= total - 1} on:click={() => dispatch('moveDown')}>
		<Icon name="chevron-down" className="h-4 w-4" />
		<span>Move down</span>
	</button>
	{#if !footerActionsShown}
		<button type="button" role="menuitem" on:click={() => dispatch('editDetails')}>
			<Icon name="pencil" className="h-4 w-4" />
			<span>Edit details</span>
		</button>
		<button type="button" role="menuitem" on:click={() => dispatch('saveAsSegment')}>
			<Icon name="save" className="h-4 w-4" />
			<span>Save as Segment</span>
		</button>
	{/if}
	<button type="button" role="menuitem" on:click={() => dispatch('replaceFromSaved')}>
		<Icon name="book-open" className="h-4 w-4" />
		<span>Replace from saved</span>
	</button>
	<button type="button" role="menuitem" on:click={() => dispatch('toggleBreak')}>
		<Icon name="layout-template" className="h-4 w-4" />
		<span>{isBreakSegment ? 'Convert to content' : 'Convert to break'}</span>
	</button>
	{#if !footerActionsShown}
		<button type="button" role="menuitem" on:click={() => dispatch('duplicate')}>
			<Icon name="copy" className="h-4 w-4" />
			<span>Duplicate</span>
		</button>
		<button type="button" role="menuitem" on:click={() => dispatch('toggleDisabled')}>
			<Icon name={segmentDisabled ? 'eyes' : 'eye-off'} className="h-4 w-4" />
			<span>{segmentDisabled ? 'Enable' : 'Disable'}</span>
		</button>
	{/if}
	<div class="my-1 border-t border-line"></div>
	<button
		type="button"
		role="menuitem"
		class="danger"
		disabled={total <= 1}
		title={total <= 1 ? 'Every prompt needs at least one segment' : undefined}
		on:click={() => dispatch('remove')}
	>
		<Icon name="trash" className="h-4 w-4" />
		<span>Delete</span>
	</button>
</div>

<style>
	.action-menu {
		position: fixed;
		z-index: 30;
		width: 11rem;
		border: 1px solid rgb(var(--line-strong));
		border-radius: 0.625rem;
		padding: 0.25rem;
		background-color: rgb(var(--surface-1));
		box-shadow: var(--shadow-floating);
	}

	.action-menu button {
		display: flex;
		width: 100%;
		min-height: 2.5rem;
		align-items: center;
		gap: 0.625rem;
		border-radius: 0.375rem;
		padding: 0.5rem 0.625rem;
		font-size: 0.8125rem;
		color: rgb(var(--fg-muted));
		text-align: left;
	}

	.action-menu button:hover:not(:disabled),
	.action-menu button:focus-visible {
		color: rgb(var(--fg));
		background-color: rgb(var(--surface-3));
		outline: none;
	}

	.action-menu button:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.action-menu button.danger {
		color: rgb(var(--danger));
	}
</style>
