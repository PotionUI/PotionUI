<script module lang="ts">
	export interface LaneMenuItem {
		name: string;
		desc?: string | null;
		icon?: string | null;
		ai?: boolean;
		danger?: boolean;
		radio?: boolean;
		checked?: boolean;
		disabled?: boolean;
		run?: () => void;
	}

	export interface LaneMenuGroup {
		label?: string | null;
		items: LaneMenuItem[];
	}

	export interface LaneMenuData {
		title: string;
		anchor?: HTMLElement | null;
		groups: LaneMenuGroup[];
	}
</script>

<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { tick } from 'svelte';

	interface Props {
		menu?: LaneMenuData | null;
		phone?: boolean;
		onclose?: () => void;
	}

	let { menu = null, phone = false, onclose }: Props = $props();

	let element = $state<HTMLDivElement>();
	let place = $state({ left: 0, top: 0, ready: false });

	function portal(node: HTMLElement) {
		document.body.appendChild(node);
		return {
			destroy() {
				node.remove();
			}
		};
	}

	function items() {
		return element ? [...element.querySelectorAll<HTMLElement>('.lane-menu-item:not([aria-disabled="true"])')] : [];
	}

	$effect(() => {
		if (!menu || !element) return;
		place = { left: 0, top: 0, ready: false };
		tick().then(() => {
			if (!element) return;
			if (!phone && menu.anchor) {
				const rect = menu.anchor.getBoundingClientRect();
				const width = element.offsetWidth;
				const height = element.offsetHeight;
				let top = rect.bottom + 6;
				if (top + height > window.innerHeight - 12) top = Math.max(12, rect.top - height - 6);
				const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
				place = { left, top, ready: true };
			} else place = { left: 0, top: 0, ready: true };
			items()[0]?.focus();
		});
	});

	function close(restore = true) {
		const anchor = menu?.anchor;
		onclose?.();
		if (restore && anchor && document.contains(anchor)) anchor.focus();
	}

	function choose(item: LaneMenuItem) {
		if (item.disabled) return;
		close(false);
		item.run?.();
	}

	function onkeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			event.preventDefault();
			event.stopPropagation();
			close();
			return;
		}
		if (event.key === 'Tab') {
			close(false);
			return;
		}
		if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
		event.preventDefault();
		const list = items();
		if (list.length === 0) return;
		const at = list.indexOf(document.activeElement as HTMLElement);
		const next = (at + (event.key === 'ArrowDown' ? 1 : -1) + list.length) % list.length;
		list[next].focus();
	}
</script>

{#if menu}
	<div use:portal>
		<button type="button" class="lane-menu-catcher {phone ? 'scrim' : ''}" aria-label="Close menu" tabindex="-1" onclick={() => close(false)}></button>
		<div
			bind:this={element}
			class="lane-menu {phone ? 'sheet' : ''}"
			style={phone ? '' : `left: ${place.left}px; top: ${place.top}px; visibility: ${place.ready ? 'visible' : 'hidden'};`}
			role="menu"
			tabindex="-1"
			aria-label={menu.title}
			{onkeydown}
		>
			<p class="lane-menu-title font-mono">{menu.title}</p>
			{#each menu.groups.filter((group: LaneMenuGroup) => group.items.length > 0) as group, groupIndex (groupIndex)}
				<div class="lane-menu-group" role="group" aria-label={group.label ?? menu.title}>
					{#if group.label}
						<p class="lane-menu-label">{group.label}</p>
					{/if}
					{#each group.items as item, index (`${groupIndex}:${index}`)}
						<button
							type="button"
							class="lane-menu-item {item.danger ? 'danger' : ''}"
							role={item.radio ? 'menuitemradio' : 'menuitem'}
							aria-checked={item.radio ? !!item.checked : undefined}
							aria-disabled={item.disabled ? 'true' : undefined}
							onclick={() => choose(item)}
						>
							{#if item.ai}
								<span class="ai-icon" aria-hidden="true"><Icon name="sparkles" className="h-3 w-3" /></span>
							{:else if item.icon}
								<span class="lane-menu-icon" aria-hidden="true"><Icon name={item.icon} className="h-4 w-4" /></span>
							{/if}
							<span class="lane-menu-text">
								<span class="lane-menu-name">{item.name}</span>
								{#if item.desc}
									<span class="lane-menu-desc">{item.desc}</span>
								{/if}
							</span>
							{#if item.checked}
								<span class="lane-menu-check" aria-hidden="true"><Icon name="check" className="h-4 w-4" /></span>
							{/if}
						</button>
					{/each}
				</div>
			{/each}
		</div>
	</div>
{/if}

<style>
	.lane-menu-catcher {
		position: fixed;
		inset: 0;
		z-index: 60;
		background: transparent;
		cursor: default;
	}
	.lane-menu-catcher.scrim {
		background: rgb(var(--scrim) / 0.5);
	}
	.lane-menu {
		position: fixed;
		z-index: 61;
		min-width: 260px;
		max-width: 320px;
		max-height: min(70vh, 560px);
		overflow-y: auto;
		padding: 6px;
		border-radius: 10px;
		border: 1px solid rgb(var(--line-strong));
		background: rgb(var(--surface-3));
		box-shadow: var(--shadow-overlay);
		scrollbar-width: thin;
	}
	.lane-menu.sheet {
		left: 0;
		right: 0;
		bottom: 0;
		top: auto;
		max-width: none;
		max-height: 80vh;
		border-radius: 10px 10px 0 0;
		padding: 6px 6px 16px;
	}
	.lane-menu-title {
		padding: 6px 10px 4px;
		font-size: 12px;
		line-height: 16px;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: rgb(var(--fg-subtle));
	}
	.lane-menu-group + .lane-menu-group {
		margin-top: 4px;
		padding-top: 4px;
		border-top: 1px solid rgb(var(--line));
	}
	.lane-menu-label {
		padding: 6px 10px 2px;
		font-size: 12px;
		line-height: 16px;
		color: rgb(var(--fg-subtle));
	}
	.lane-menu-item {
		display: flex;
		width: 100%;
		align-items: center;
		gap: 10px;
		min-height: 36px;
		padding: 6px 10px;
		border-radius: 4px;
		text-align: left;
		font-size: 13px;
		color: rgb(var(--fg));
	}
	.sheet .lane-menu-item {
		min-height: 48px;
	}
	.lane-menu-item:hover,
	:global(:where(html:not([data-input='pointer']))) .lane-menu-item:focus-visible {
		background: rgb(var(--surface-2));
		outline: none;
	}
	:global(:where(html:not([data-input='pointer']))) .lane-menu-item:focus-visible {
		box-shadow: inset 0 0 0 2px rgb(var(--signal));
	}
	.lane-menu-item[aria-disabled='true'] {
		color: rgb(var(--fg-disabled));
		cursor: default;
	}
	.lane-menu-item[aria-disabled='true']:hover {
		background: transparent;
	}
	.lane-menu-icon {
		display: inline-flex;
		color: rgb(var(--fg-muted));
	}
	.lane-menu-item[aria-disabled='true'] .lane-menu-icon {
		color: rgb(var(--fg-disabled));
	}
	.lane-menu-text {
		display: flex;
		min-width: 0;
		flex: 1;
		flex-direction: column;
	}
	.lane-menu-name {
		overflow: hidden;
		white-space: nowrap;
		text-overflow: ellipsis;
	}
	.lane-menu-item[aria-checked='true'] .lane-menu-name {
		color: rgb(var(--signal));
	}
	.lane-menu-desc {
		font-size: 12px;
		line-height: 16px;
		color: rgb(var(--fg-subtle));
	}
	.lane-menu-item[aria-disabled='true'] .lane-menu-desc {
		color: rgb(var(--fg-disabled));
	}
	.lane-menu-check {
		display: inline-flex;
		color: rgb(var(--signal));
	}
	.lane-menu-item.danger .lane-menu-name,
	.lane-menu-item.danger .lane-menu-icon {
		color: rgb(var(--danger));
	}
	.ai-icon {
		display: inline-grid;
		place-items: center;
		width: 16px;
		height: 16px;
		flex: none;
		border-radius: 4px;
		color: rgb(var(--on-scrim));
		background: linear-gradient(145deg, rgb(var(--ai-1)), rgb(var(--ai-2)) 55%, rgb(var(--ai-3)));
	}
</style>
