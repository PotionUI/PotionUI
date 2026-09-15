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

	const levelDot: Record<AppNotification['level'], string> = {
		success: 'bg-success',
		error: 'bg-danger',
		warning: 'bg-warning',
		info: 'bg-info'
	};

	let dotClass = $derived(levelDot[notification.level] ?? levelDot.info);

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
	class="group relative flex items-start gap-2.5 pl-4 pr-9 py-2.5 border-b border-line hover:bg-surface-2/50 transition-colors cursor-default"
	role="button"
	tabindex="0"
	onclick={handleClick}
	onkeydown={(e) => (e.key === 'Enter' || e.key === ' ') && handleClick()}
>
	<span class="w-1.5 h-1.5 rounded-full mt-1.5 flex-shrink-0 {dotClass}" aria-hidden="true"></span>

	<div class="flex-1 min-w-0">
		<div class="flex items-baseline gap-1.5">
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
	</div>

	<span
		class="flex-shrink-0 font-mono text-2xs text-fg-subtle tabular-nums uppercase tracking-wide group-hover:opacity-0 transition-opacity"
	>
		{timeAgo(notification.created_at)}
	</span>

	<div class="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
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
