<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import type { Segment } from '$lib/types/segments';
	import { segmentMenuActions, SEGMENT_ACTION_GROUPS, type SegmentAction } from '$lib/utils/segmentActions';

	export let segment: Segment;
	export let index: number;
	export let total: number;
	export let segmentDisabled = false;
	export let ariaLabel: string;
	export let style = '';
	export let pinnedIds: Set<string> = new Set();
	export let hasPromptSyntax = false;
	export let hasPromptResources = false;

	const dispatch = createEventDispatcher<{ run: string; togglePin: string }>();

	$: actions = segmentMenuActions(segment, { index, total, segmentDisabled, hasPromptSyntax, hasPromptResources });
	$: byId = new Map(actions.map((action) => [action.id, action]));
	$: groups = SEGMENT_ACTION_GROUPS.map((ids) => ids.map((id) => byId.get(id)).filter(Boolean) as SegmentAction[]).filter(
		(group) => group.length > 0
	);
</script>

<div class="floating segment-menu action-menu" role="menu" aria-label={ariaLabel} {style}>
	{#each groups as group}
		<div class="menu-group">
			{#each group as action (action.id)}
				<div class="menu-row">
					<button
						type="button"
						role="menuitem"
						class="menu-item"
						disabled={action.disabled}
						on:click={() => dispatch('run', action.id)}
					>
						{#if action.glyph}
							<span class="icon menu-glyph {action.glyphClass}">{action.glyph}</span>
						{:else}
							<svg class="icon"><use href={`#i-${action.icon}`} /></svg>
						{/if}
						<span>{action.label}</span>
					</button>
					<button
						type="button"
						class="menu-pin"
						aria-pressed={pinnedIds.has(action.id)}
						aria-label={pinnedIds.has(action.id) ? `Unpin ${action.label}` : `Pin ${action.label}`}
						on:click|stopPropagation={() => dispatch('togglePin', action.id)}
					>
						<svg class="icon"><use href="#i-pin" /></svg>
					</button>
				</div>
			{/each}
		</div>
	{/each}
</div>

<style>
	.action-menu {
		position: fixed;
		z-index: 30;
	}

	.action-menu .menu-item:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.menu-row {
		display: flex;
		align-items: center;
		gap: 2px;
	}

	.menu-row .menu-item {
		flex: 1 1 auto;
	}

	.menu-pin {
		width: 28px;
		height: 28px;
		flex: 0 0 auto;
		display: grid;
		place-items: center;
		color: rgb(var(--fg-subtle));
		background: transparent;
		border-radius: 4px;
	}

	.menu-pin:hover {
		color: rgb(var(--fg));
		background: rgb(var(--surface-3));
	}

	.menu-pin[aria-pressed='true'] {
		color: rgb(var(--signal));
	}

	.menu-pin .icon {
		width: 14px;
		height: 14px;
	}
</style>
