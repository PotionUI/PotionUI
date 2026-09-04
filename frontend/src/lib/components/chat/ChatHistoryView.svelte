<script lang="ts">
	// The rail: brand + search toggle, new-conversation button, mode filter
	// chips (real functionality; the prototype's single-mode history never
	// needs them), and the grouped session list. Markup ported verbatim from
	// the mock's <aside class="history-rail"> (chat-rework BRIEF2) — this
	// component IS that aside; UnifiedAIChat renders it as a direct child of
	// .chat-shell so its own `&.rail-collapsed .history-rail` CSS rule applies.
	import { onMount, tick } from 'svelte';
	import { api } from '$lib/services/api';
	import { chatModes, resolveModeName } from '$lib/stores/chatModes';
	import { chatSession } from '$lib/stores/chatSession';
	import { groupSessionsByDate } from '$lib/utils/chat';
	import { timeAgo } from '$lib/utils/relativeTime';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Logo from '$lib/components/brand/Logo.svelte';
	import { logger } from '$lib/utils/logger';
	import type { ChatSessionResponse } from '$lib/types/api';

	export let onOpenSession: (id: string) => void | Promise<void>;
	export let onNewChat: () => void;
	export let onSessionDeleted: ((id: string) => void) | undefined = undefined;

	// `groupSessionsByDate`'s bucket labels read naturally in a settings table;
	// the rail wants the shorter, more casual phrasing the mock uses instead.
	const GROUP_LABELS: Record<string, string> = { 'This week': 'Previous 7 days' };

	let sessions: ChatSessionResponse[] = [];
	let loading = true;
	let search = '';
	let searchOpen = false;
	let searchInputEl: HTMLInputElement | undefined;
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

	async function toggleSearch() {
		searchOpen = !searchOpen;
		if (searchOpen) {
			await tick();
			searchInputEl?.focus();
		} else if (search || modeFilter) {
			search = '';
			modeFilter = null;
			fetchSessions();
		}
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

<aside class="history-rail">
	<div class="rail-head">
		<div class="brand-orb"><Logo size={20} /></div>
		<div class="brand-name">PotionAI</div>
		<button
			class="tiny-button"
			class:active={searchOpen}
			aria-label="Search conversations"
			title="Search conversations"
			on:click={toggleSearch}
		>
			<svg class="icon"><use href="#i-search" /></svg>
		</button>
	</div>

	{#if searchOpen}
		<div class="rail-search">
			<input
				bind:this={searchInputEl}
				type="text"
				placeholder="Search conversations…"
				bind:value={search}
				on:input={handleSearchInput}
			/>
			<span class="rail-search-select">
				<select
					aria-label="Conversation type"
					value={modeFilter ?? ''}
					on:change={(e) => setModeFilter(e.currentTarget.value || null)}
				>
					<option value="">All types</option>
					{#each $chatModes.modes as mode}
						<option value={mode.id}>{mode.name}</option>
					{/each}
				</select>
				<svg class="icon chevron"><use href="#i-chevron" /></svg>
			</span>
		</div>
	{/if}

	<button class="new-chat-button" title="New chat" on:click={onNewChat}>
		<span style="display:flex;align-items:center;gap:8px"
			><svg class="icon"><use href="#i-plus" /></svg>New conversation</span
		>
	</button>

	<div class="history-scroll">
		{#if loading}
			<div class="flex items-center justify-center py-16">
				<Spinner size="sm" />
			</div>
		{:else if sessions.length === 0}
			<div class="session-empty">
				{#if search.trim()}
					No conversations match &ldquo;{search.trim()}&rdquo;
				{:else}
					No conversations yet
				{/if}
			</div>
		{:else}
			{#each groups as group}
				<div class="history-group">{GROUP_LABELS[group.label] ?? group.label}</div>
				{#each group.sessions as session (session.id)}
					<!-- A `<button>` cannot legally contain another focusable control (the
					     delete affordance) — a `div[role=button]` keeps `.session`'s exact
					     mock styling (class selectors only) while staying valid/accessible. -->
					<div
						class="session"
						class:active={activeSessionId === session.id}
						role="button"
						tabindex="0"
						data-title={session.name || 'New conversation'}
						on:click={() => onOpenSession(session.id)}
						on:keydown={(e) => e.key === 'Enter' && onOpenSession(session.id)}
					>
						<span class="session-title">{session.name || 'New conversation'}</span>
						<span class="session-meta">
							<span class="session-mode">{modeName(session.mode)}</span>
							<span>{timeAgo(session.updated_at || session.created_at)}</span>
						</span>
						<button
							type="button"
							class="session-delete"
							title="Delete conversation"
							on:click={(e) => handleDelete(session.id, e)}
						>
							<svg class="icon"><use href="#i-trash" /></svg>
						</button>
					</div>
				{/each}
			{/each}
		{/if}
	</div>
</aside>
