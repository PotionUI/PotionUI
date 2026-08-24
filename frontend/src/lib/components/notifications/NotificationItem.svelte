<script lang="ts">
	import IconButton from '$lib/components/ui/IconButton.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { copyText } from '$lib/utils/clipboard';
	import type { AppNotification } from '$lib/services/api/notifications';

	let {
		notification,
		onMarkRead,
		onRemove
	}: {
		notification: AppNotification;
		onMarkRead: (id: string) => void;
		onRemove: (id: string) => void;
	} = $props();

	// Per-level token colours + iconography (semantic tokens only).
	const levelStyles: Record<AppNotification['level'], { color: string; path: string }> = {
		success: { color: 'text-success', path: 'M5 13l4 4L19 7' },
		error: {
			color: 'text-danger',
			path: 'M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'
		},
		warning: {
			color: 'text-warning',
			path: 'M12 9v2m0 4h.01M5.07 19h13.86a2 2 0 001.74-2.99L13.73 4a2 2 0 00-3.46 0L3.33 16.01A2 2 0 005.07 19z'
		},
		info: {
			color: 'text-info',
			path: 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z'
		}
	};

	let style = $derived(levelStyles[notification.level] ?? levelStyles.info);

	let detail = $derived(
		typeof notification.metadata?.detail === 'string' && notification.metadata.detail.trim()
			? notification.metadata.detail
			: null
	);

	let copied = $state(false);

	function handleClick() {
		if (!notification.read) onMarkRead(notification.id);
	}

	async function handleCopyDetail(e: MouseEvent) {
		e.stopPropagation();
		if (!detail) return;
		const ok = await copyText(detail);
		if (ok) {
			copied = true;
			setTimeout(() => (copied = false), 1500);
		}
	}
</script>

<div
	class="group relative flex items-start gap-3 px-4 py-3 border-b border-line transition-colors cursor-default
		{notification.read ? 'bg-transparent hover:bg-surface-2/50' : 'bg-surface-2 hover:bg-surface-3'}"
	role="button"
	tabindex="0"
	onclick={handleClick}
	onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && handleClick()}
>
	<!-- Level icon -->
	<div class="flex-shrink-0 mt-0.5 {style.color}">
		<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d={style.path} />
		</svg>
	</div>

	<!-- Body -->
	<div class="flex-1 min-w-0">
		<div class="flex items-center gap-2">
			{#if !notification.read}
				<span class="w-1.5 h-1.5 rounded-full bg-signal flex-shrink-0" aria-hidden="true"></span>
			{/if}
			<p class="text-sm font-medium text-fg truncate">{notification.title}</p>
		</div>
		{#if notification.message}
			<p class="text-xs text-fg-muted mt-0.5 leading-snug break-words">{notification.message}</p>
		{/if}
		{#if detail}
			<details class="mt-1.5">
				<summary
					class="text-2xs text-fg-subtle cursor-pointer select-none w-fit"
					onclick={(e) => e.stopPropagation()}
					onkeydown={(e) => e.stopPropagation()}
				>
					Details
				</summary>
				<div class="relative mt-1.5">
					<pre
						class="font-mono text-2xs bg-surface-3 text-fg-muted rounded px-2 py-1.5 pr-8 overflow-x-auto whitespace-pre-wrap break-words max-h-40 overflow-y-auto">{detail}</pre>
					<div class="absolute top-1 right-1">
						<Tooltip text={copied ? 'Copied' : 'Copy error'} position="top">
							<IconButton
								icon={copied ? 'check' : 'copy'}
								label="Copy error"
								size="sm"
								class={copied ? 'text-success' : ''}
								onclick={handleCopyDetail}
							/>
						</Tooltip>
					</div>
				</div>
			</details>
		{/if}
		<p class="text-2xs text-fg-subtle mt-1 font-mono tabular-nums uppercase tracking-wide">
			{timeAgo(notification.created_at)}
		</p>
	</div>

	<!-- Dismiss -->
	<div class="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
		<IconButton
			icon="close"
			label="Dismiss notification"
			size="sm"
			onclick={(e) => {
				e.stopPropagation();
				onRemove(notification.id);
			}}
		/>
	</div>
</div>
