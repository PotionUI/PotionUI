<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { logger, getErrorMessage } from '$lib/utils/logger';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge, Button, Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import { authStore } from '$lib/stores/auth';
	import { toasts } from '$lib/stores/toast';
	import { inspirationsStore } from '$lib/stores/inspirations';
	import { getBackends, type Backend } from '$lib/services/admin-api';
	import { tabsStore } from '$lib/stores/tabs';
	import { buildImportBundleTabData } from '$lib/utils/historyReuse';
	import { buildInspirationReuseSource, formatOmittedFieldsHint } from '$lib/inspirations/reuseAdapter';
	import type { PresetInfo } from '$lib/types/api';
	import { canModerateInspiration } from '$lib/inspirations/inspirationCardMeta';
	import { canDeleteInspirationComment } from '$lib/inspirations/commentPermissions';
	import { timeAgo } from '$lib/utils/relativeTime';
	import type { InspirationDto, InspirationComment } from '$lib/services/api/inspirations';
	import InspirationCollectionPicker from './InspirationCollectionPicker.svelte';

	export let item: InspirationDto;
	export let onClose: () => void;
	export let onDeleted: (id: string) => void;

	$: viewer = $authStore.user;
	$: canModerate = canModerateInspiration(item, viewer);

	let fileIndex = 0;
	$: media = item.media;
	$: currentMedia = media[fileIndex];
	$: canPrev = fileIndex > 0;
	$: canNext = fileIndex < media.length - 1;
	$: isVideo = (currentMedia?.type ?? '').toLowerCase() === 'video';

	function goPrev() {
		if (canPrev) fileIndex--;
	}
	function goNext() {
		if (canNext) fileIndex++;
	}

	// --- Save to library --------------------------------------------------
	let saving = false;
	$: saved = item.saved_by_me;
	$: saveCount = item.save_count;

	async function toggleSave() {
		if (saving) return;
		saving = true;
		try {
			if (saved) {
				const response = await api.unsaveInspiration(item.id);
				if (response.success) {
					inspirationsStore.patchItem(item.id, {
						saved_by_me: false,
						save_count: Math.max(0, saveCount - 1)
					});
				} else {
					toasts.error('Could not remove the save.');
				}
			} else {
				const response = await api.saveInspirationToLibrary(item.id);
				if (response.success) {
					inspirationsStore.patchItem(item.id, {
						saved_by_me: true,
						save_count: response.data?.save_count ?? saveCount + 1
					});
					toasts.success('Saved to your library');
				} else {
					toasts.error('Could not save this inspiration.');
				}
			}
		} catch (e) {
			logger.error('Toggle inspiration save failed:', getErrorMessage(e));
			toasts.error('Could not update the save.');
		} finally {
			saving = false;
		}
	}

	// --- Reuse ---------------------------------------------------------------
	let availableBackends: Backend[] | null = null;
	let availablePresets: PresetInfo[] | null = null;
	let reusing = false;

	async function handleReuse() {
		if (reusing) return;
		reusing = true;
		try {
			const paramsResponse = await api.getInspirationParams(item.id);
			if (!paramsResponse.success || !paramsResponse.data) {
				toasts.error("Could not load this inspiration's settings.");
				return;
			}

			const presetId = paramsResponse.data.preset_id;
			if (presetId) {
				if (!availablePresets) {
					try {
						const presetsResponse = await api.listPresets();
						availablePresets = presetsResponse.data ?? [];
					} catch (e) {
						logger.error('Failed to load presets for inspiration reuse:', getErrorMessage(e));
						availablePresets = [];
					}
				}
				const presetAvailable = availablePresets.some((p) => p.id === presetId);
				if (!presetAvailable) {
					const presetLabel = paramsResponse.data.preset_name ?? presetId;
					toasts.error(`This inspiration requires preset "${presetLabel}", which isn't available.`);
					return;
				}
			}

			if (!availableBackends) {
				try {
					const backendsResponse = await getBackends();
					availableBackends = backendsResponse.data ?? [];
				} catch (e) {
					logger.error('Failed to load backends for inspiration reuse:', getErrorMessage(e));
					availableBackends = [];
				}
			}
			const source = buildInspirationReuseSource(paramsResponse.data);
			const { tabData, backendUnavailable } = buildImportBundleTabData(source);
			const tabName = `Reused: ${paramsResponse.data.preset_name ?? item.title}`;
			tabsStore.addTabWithData(tabName, tabData);
			if (backendUnavailable) {
				toasts.info('Original backend is no longer available — using the default backend.');
			}
			const omittedHint = formatOmittedFieldsHint(paramsResponse.data.omitted_fields ?? []);
			if (omittedHint) {
				toasts.info(omittedHint);
			}
			onClose();
			goto('/generate');
		} finally {
			reusing = false;
		}
	}

	// --- Delete ----------------------------------------------------------
	let confirmingDelete = false;
	let deleting = false;

	async function confirmDelete() {
		deleting = true;
		try {
			const response = await inspirationsStore.remove(item.id);
			if (response.success) {
				onDeleted(item.id);
			} else {
				toasts.error('Could not delete this inspiration.');
			}
		} finally {
			deleting = false;
			confirmingDelete = false;
		}
	}

	// --- Add to collection -------------------------------------------------
	let showCollectionPicker = false;

	// --- Comments ----------------------------------------------------------
	const MAX_COMMENT_LENGTH = 2000;
	let comments: InspirationComment[] = [];
	let commentsLoading = true;
	let commentBody = '';
	let commentBusy = false;
	$: commentCount = item.comment_count;

	onMount(loadComments);

	async function loadComments() {
		commentsLoading = true;
		try {
			const response = await api.listInspirationComments(item.id);
			if (response.success && response.data) {
				comments = response.data.items;
			}
		} catch (e) {
			logger.error('Failed to load inspiration comments:', getErrorMessage(e));
		} finally {
			commentsLoading = false;
		}
	}

	async function submitComment() {
		const body = commentBody.trim();
		if (!body || commentBusy) return;
		if (body.length > MAX_COMMENT_LENGTH) {
			toasts.error(`Comments are limited to ${MAX_COMMENT_LENGTH} characters.`);
			return;
		}
		commentBusy = true;
		try {
			const response = await api.addInspirationComment(item.id, body);
			if (response.success) {
				commentBody = '';
				inspirationsStore.patchItem(item.id, { comment_count: commentCount + 1 });
				await loadComments();
			} else {
				toasts.error('Could not post that comment.');
			}
		} catch (e) {
			logger.error('Failed to post inspiration comment:', getErrorMessage(e));
			toasts.error('Could not post that comment.');
		} finally {
			commentBusy = false;
		}
	}

	async function deleteComment(comment: InspirationComment) {
		try {
			const response = await api.deleteInspirationComment(item.id, comment.id);
			if (response.success) {
				comments = comments.filter((c) => c.id !== comment.id);
				inspirationsStore.patchItem(item.id, { comment_count: Math.max(0, commentCount - 1) });
			} else {
				toasts.error('Could not delete that comment.');
			}
		} catch (e) {
			logger.error('Failed to delete inspiration comment:', getErrorMessage(e));
			toasts.error('Could not delete that comment.');
		}
	}

	function handleCommentKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			submitComment();
		}
	}

	function formatDate(dateString?: string) {
		if (!dateString) return 'N/A';
		return new Date(dateString).toLocaleString();
	}
</script>

<BaseModal isOpen={true} size="xl" title={item.title || 'Inspiration'} on:close={onClose}>
	<div class="p-4 md:p-6 flex flex-col gap-4">
		<!-- Media -->
		<div
			class="relative rounded-lg border border-line-strong bg-black overflow-hidden flex items-center justify-center min-h-[16rem] max-h-[55vh]"
		>
			{#if canPrev}
				<button
					class="absolute left-3 top-1/2 -translate-y-1/2 z-10 p-2.5 rounded bg-black/70 hover:bg-black/80 text-white transition-colors"
					on:click={goPrev}
					aria-label="Previous file"
				>
					<Icon name="chevron-left" className="w-5 h-5" />
				</button>
			{/if}
			{#if canNext}
				<button
					class="absolute right-3 top-1/2 -translate-y-1/2 z-10 p-2.5 rounded bg-black/70 hover:bg-black/80 text-white transition-colors"
					on:click={goNext}
					aria-label="Next file"
				>
					<Icon name="chevron-right" className="w-5 h-5" />
				</button>
			{/if}
			{#if media.length > 1}
				<div
					class="absolute top-3 left-3 z-10 bg-black/70 backdrop-blur-sm text-white px-2.5 py-1 rounded text-xs font-mono tabular-nums"
				>
					{fileIndex + 1} / {media.length}
				</div>
			{/if}

			{#if currentMedia}
				{#if isVideo}
					<video src={currentMedia.url} class="max-h-[55vh] max-w-full" controls playsinline>
						<track kind="captions" />
					</video>
				{:else}
					<img src={currentMedia.url} alt={item.title} class="max-h-[55vh] max-w-full object-contain" />
				{/if}
			{/if}
		</div>

		<!-- Title / author / description -->
		<div class="flex flex-wrap items-start justify-between gap-3">
			<div class="min-w-0">
				<h2 class="text-base font-semibold text-fg break-words">{item.title || 'Untitled'}</h2>
				<div class="flex items-center gap-1.5 mt-1">
					{#if item.author.avatar_url}
						<img src={item.author.avatar_url} alt="" class="w-5 h-5 rounded-full object-cover" />
					{:else}
						<span
							class="w-5 h-5 rounded-full bg-surface-3 text-fg-subtle flex items-center justify-center text-2xs font-medium"
						>
							{item.author.username.charAt(0).toUpperCase()}
						</span>
					{/if}
					<span class="text-xs text-fg-muted">{item.author.username}</span>
					<span class="text-fg-subtle">·</span>
					<span class="font-mono tabular-nums text-2xs text-fg-subtle">{formatDate(item.created_at)}</span>
				</div>
				{#if item.description}
					<p class="text-sm text-fg-muted mt-2 max-w-2xl break-words">{item.description}</p>
				{/if}
			</div>

			<Badge variant="neutral" class="inline-flex items-center gap-1.5 flex-shrink-0">
				<Icon name="save" className="w-3 h-3" />
				{saveCount}
			</Badge>
		</div>

		<!-- Params preview -->
		{#if item.params_preview.length > 0}
			<div class="flex flex-wrap gap-1.5">
				{#each item.params_preview as param (param.name)}
					<span
						class="inline-flex items-center gap-1 px-2 py-1 rounded bg-surface-2 border border-line font-mono text-2xs text-fg-muted"
					>
						<span class="text-fg-subtle">{param.name}:</span>
						{typeof param.value === 'object' ? JSON.stringify(param.value) : String(param.value)}
					</span>
				{/each}
			</div>
		{/if}

		<!-- Actions -->
		<div class="flex flex-wrap items-center gap-2 pt-2 border-t border-line">
			<Button
				variant={saved ? 'primary' : 'secondary'}
				size="sm"
				icon="save"
				loading={saving}
				disabled={saving}
				onclick={toggleSave}
			>
				{saved ? 'Saved' : 'Save to library'}
			</Button>

			<Button variant="secondary" size="sm" icon="refresh" loading={reusing} disabled={reusing} onclick={handleReuse}>
				Reuse
			</Button>

			<div class="relative">
				<Button
					variant="secondary"
					size="sm"
					icon="folder-plus"
					onclick={() => (showCollectionPicker = !showCollectionPicker)}
				>
					Add to collection
				</Button>
				{#if showCollectionPicker}
					<InspirationCollectionPicker
						inspirationId={item.id}
						onClose={() => (showCollectionPicker = false)}
					/>
				{/if}
			</div>

			<div class="flex-1"></div>

			{#if canModerate}
				<Button variant="danger" size="sm" icon="trash" onclick={() => (confirmingDelete = true)}>
					Delete
				</Button>
			{/if}
		</div>

		<!-- Comments -->
		<div class="pt-2 border-t border-line">
			<div class="flex items-center gap-2 mb-3">
				<Icon name="chat" className="w-4 h-4 text-fg-muted" />
				<h3 class="text-sm font-semibold text-fg">Comments</h3>
				<Badge variant="neutral" size="sm">{commentCount}</Badge>
			</div>

			{#if commentsLoading}
				<div class="flex items-center justify-center py-4">
					<Spinner size="sm" />
				</div>
			{:else}
				<div class="space-y-3 mb-3">
					{#each comments as comment (comment.id)}
						<div class="flex items-start gap-2">
							{#if comment.user.avatar_url}
								<img
									src={comment.user.avatar_url}
									alt=""
									class="w-6 h-6 rounded-full object-cover flex-shrink-0"
								/>
							{:else}
								<span
									class="w-6 h-6 rounded-full bg-surface-3 text-fg-subtle flex items-center justify-center text-2xs font-medium flex-shrink-0"
								>
									{comment.user.username.charAt(0).toUpperCase()}
								</span>
							{/if}
							<div class="min-w-0 flex-1 bg-surface-2 rounded-lg px-3 py-2">
								<div class="flex items-center gap-2">
									<span class="text-xs font-medium text-fg">{comment.user.username}</span>
									<span class="font-mono tabular-nums text-2xs text-fg-subtle">{timeAgo(comment.created_at)}</span>
									{#if canDeleteInspirationComment(comment, viewer)}
										<button
											type="button"
											class="ml-auto p-0.5 hover:bg-surface-3 rounded transition-colors"
											on:click={() => deleteComment(comment)}
											aria-label="Delete comment"
										>
											<Icon name="trash" className="w-3 h-3 text-fg-subtle" />
										</button>
									{/if}
								</div>
								<p class="text-sm text-fg mt-0.5 break-words whitespace-pre-wrap">{comment.body}</p>
							</div>
						</div>
					{:else}
						<p class="text-xs text-fg-subtle">No comments yet.</p>
					{/each}
				</div>
			{/if}

			<div class="flex items-start gap-2">
				<input
					type="text"
					class="input text-sm flex-1"
					placeholder="Add a comment…"
					maxlength={MAX_COMMENT_LENGTH}
					bind:value={commentBody}
					on:keydown={handleCommentKeydown}
					disabled={commentBusy}
				/>
				<Button
					variant="secondary"
					size="sm"
					loading={commentBusy}
					disabled={commentBusy || !commentBody.trim()}
					onclick={submitComment}
				>
					Post
				</Button>
			</div>
		</div>
	</div>
</BaseModal>

{#if confirmingDelete}
	<ConfirmModal
		isOpen={true}
		title="Delete inspiration"
		message={`Delete "${item.title || 'this inspiration'}" from Inspirations? This cannot be undone.`}
		variant="danger"
		busy={deleting}
		on:confirm={confirmDelete}
		on:cancel={() => (confirmingDelete = false)}
	/>
{/if}
