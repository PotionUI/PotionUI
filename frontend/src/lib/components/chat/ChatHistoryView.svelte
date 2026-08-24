<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/services/api';
	import { chatModes, resolveModeName } from '$lib/stores/chatModes';
	import { chatSession } from '$lib/stores/chatSession';
	import { groupSessionsByDate } from '$lib/utils/chat';
	import { timeAgo } from '$lib/utils/relativeTime';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import { logger } from '$lib/utils/logger';
	import type { ChatSessionResponse } from '$lib/types/api';

	export let onOpenSession: (id: string) => void | Promise<void>;
	export let onNewChat: () => void;
	export let onSessionDeleted: ((id: string) => void) | undefined = undefined;
	// Rail presentation: narrower rows, no message-count/mode badge, search
	// and "New chat" stacked instead of side by side.
	export let compact = false;

	let sessions: ChatSessionResponse[] = [];
	let loading = true;
	let search = '';
	let modeFilter: string | null = null;
	let searchTimer: ReturnType<typeof setTimeout> | null = null;
	let requestSeq = 0;

	$: activeSessionId = $chatSession.sessionId;
	$: groups = groupSessionsByDate(
		sessions.map((s) => ({
			...s,
			created_at: s.created_at ?? undefined,
			updated_at: s.updated_at ?? undefined
		}))
	);

	function modeName(modeId: string | undefined): string {
		if (!modeId) return '';
		return resolveModeName(modeId, $chatModes.modes);
	}

	async function fetchSessions() {
		const seq = ++requestSeq;
		loading = true;
		try {
			const response = await api.getChatSessions({
				mode: modeFilter || undefined,
				search: search.trim() || undefined,
				limit: 50
			});
			if (seq !== requestSeq) return; // a newer request superseded this one
			sessions = (response.data?.sessions || []).filter((s) => s.status === 'active');
		} catch (err) {
			logger.error('Failed to load chat history:', err);
			if (seq === requestSeq) sessions = [];
		} finally {
			if (seq === requestSeq) loading = false;
		}
	}

	function handleSearchInput() {
		if (searchTimer) clearTimeout(searchTimer);
		searchTimer = setTimeout(fetchSessions, 250);
	}

	function setModeFilter(mode: string | null) {
		modeFilter = mode;
		fetchSessions();
	}

	async function handleDelete(id: string, event: MouseEvent) {
		event.stopPropagation();
		try {
			const response = await api.deleteChatSession(id);
			if (response.success) {
				sessions = sessions.filter((s) => s.id !== id);
				onSessionDeleted?.(id);
			}
		} catch (err) {
			logger.error('Failed to delete session:', err);
		}
	}

	onMount(() => {
		fetchSessions();
		return () => {
			if (searchTimer) clearTimeout(searchTimer);
		};
	});
</script>

<div class="flex-1 flex flex-col min-h-0 bg-canvas">
	<!-- Controls -->
	<div class="{compact ? 'px-2.5 pt-2.5' : 'px-4 pt-4'} pb-2 space-y-2 flex-shrink-0">
		<div class="{compact ? 'flex flex-col gap-2' : 'flex items-center gap-2'}">
			<div class="relative flex-1">
				<svg class="w-3.5 h-3.5 text-fg-subtle absolute left-2.5 top-1/2 -translate-y-1/2 pointer-events-none" fill="none" stroke="currentColor" viewBox="0 0 24 24">
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
				</svg>
				<input
					type="text"
					placeholder="Search conversations…"
					bind:value={search}
					on:input={handleSearchInput}
					class="w-full pl-8 pr-3 py-1.5 text-sm bg-surface-1 border border-line rounded text-fg placeholder:text-fg-subtle focus:outline-none focus:border-signal transition-colors"
				/>
			</div>
			<Button variant="primary" size="sm" icon="plus" onclick={onNewChat} class={compact ? 'w-full' : ''}>New chat</Button>
		</div>

		{#if $chatModes.modes.length > 1}
			<div class="flex items-center gap-1.5 flex-wrap">
				<button
					type="button"
					class="px-2 py-0.5 text-xs rounded border transition-colors {modeFilter === null
						? 'bg-signal/10 text-signal border-signal/25'
						: 'text-fg-subtle border-line hover:text-fg-muted hover:bg-surface-2'}"
					on:click={() => setModeFilter(null)}
				>
					All
				</button>
				{#each $chatModes.modes as mode}
					<button
						type="button"
						class="px-2 py-0.5 text-xs rounded border transition-colors {modeFilter === mode.id
							? 'bg-signal/10 text-signal border-signal/25'
							: 'text-fg-subtle border-line hover:text-fg-muted hover:bg-surface-2'}"
						on:click={() => setModeFilter(mode.id)}
					>
						{mode.name}
					</button>
				{/each}
			</div>
		{/if}
	</div>

	<!-- Conversation list -->
	<div class="flex-1 overflow-y-auto px-2 pb-4 min-h-0 scrollbar-thin scrollbar-thumb-[rgb(var(--line-strong))] scrollbar-track-transparent">
		{#if loading}
			<div class="flex items-center justify-center py-16">
				<Spinner size="sm" />
			</div>
		{:else if sessions.length === 0}
			<div class="flex flex-col items-center justify-center py-16 text-center px-6">
				<div class="w-12 h-12 rounded-lg bg-surface-1 border border-line flex items-center justify-center mb-4">
					<svg class="w-6 h-6 text-fg-subtle" fill="none" stroke="currentColor" viewBox="0 0 24 24">
						<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
					</svg>
				</div>
				{#if search.trim()}
					<p class="text-sm text-fg-muted">No conversations match &ldquo;{search.trim()}&rdquo;</p>
				{:else}
					<p class="text-sm text-fg-muted mb-3">No conversations yet</p>
					<Button variant="primary" size="sm" icon="plus" onclick={onNewChat}>New chat</Button>
				{/if}
			</div>
		{:else}
			{#each groups as group}
				<div class="px-2 {compact ? 'pt-3' : 'pt-4'} pb-1.5">
					<span class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">{group.label}</span>
				</div>
				{#each group.sessions as session (session.id)}
					<div
						class="group w-full {compact ? 'px-2 py-1.5' : 'px-3 py-2'} rounded cursor-pointer transition-colors flex items-center gap-2 {activeSessionId === session.id
							? 'border-l-2 border-signal bg-surface-2'
							: 'border-l-2 border-transparent hover:bg-surface-2'}"
						role="button"
						tabindex="0"
						on:click={() => onOpenSession(session.id)}
						on:keydown={(e) => e.key === 'Enter' && onOpenSession(session.id)}
					>
						<div class="flex-1 min-w-0">
							<div class="{compact ? 'text-xs' : 'text-sm'} font-medium text-fg truncate">
								{session.name || 'New conversation'}
							</div>
							{#if compact}
								<div class="flex items-center gap-1.5 mt-0.5">
									<Badge variant="neutral" size="sm" class="font-mono uppercase">{modeName(session.mode)}</Badge>
									<span class="font-mono tabular-nums text-2xs text-fg-subtle uppercase">
										{timeAgo(session.updated_at || session.created_at)}
									</span>
								</div>
							{:else}
								<div class="flex items-center gap-2 mt-0.5">
									<Badge variant="neutral" size="sm">{modeName(session.mode)}</Badge>
									<span class="font-mono tabular-nums text-2xs text-fg-subtle uppercase">
										{timeAgo(session.updated_at || session.created_at)}
									</span>
									<span class="font-mono tabular-nums text-2xs text-fg-subtle">
										{session.message_count ?? 0} msg
									</span>
								</div>
							{/if}
						</div>
						<button
							type="button"
							title="Delete conversation"
							class="p-1.5 rounded text-fg-subtle hover:text-danger hover:bg-surface-3/50 opacity-0 group-hover:opacity-100 focus:opacity-100 flex-shrink-0 transition-all"
							on:click={(e) => handleDelete(session.id, e)}
						>
							<svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
								<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
							</svg>
						</button>
					</div>
				{/each}
			{/each}
		{/if}
	</div>
</div>
