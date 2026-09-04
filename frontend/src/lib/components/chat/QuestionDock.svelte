<script lang="ts">
	/**
	 * Docked question queue, ported verbatim from the mock's
	 * `.question-dock-wrap` (design-proposals/llm-chat-concept.html). Split out
	 * of ApprovalDock so the question anatomy can match the mock exactly while
	 * approvals keep their own (non-mocked) compact/expand shell. Mounted by
	 * UnifiedAIChat only once the approval queue has drained — approvals gate
	 * side effects, questions are optional, so they rank first.
	 */
	import { tick } from 'svelte';
	import { processMarkdown } from '$lib/utils/markdown';
	import type { UnifiedChatMessageData } from '$lib/types/chat';
	import { composeQuestionAnswer, deriveQuestionQueue, dismissedQuestions } from '$lib/chat/questionQueue';

	export let messages: UnifiedChatMessageData[] = [];
	/** Sends a docked question's answer as a normal user turn (see UnifiedAIChat's sendMessage). */
	export let onAnswerQuestion: ((text: string) => void | Promise<void>) | undefined = undefined;

	let otherInputEl: HTMLInputElement | undefined;

	$: queue = deriveQuestionQueue(messages, $dismissedQuestions);
	$: current = queue[0] ?? null;
	$: remaining = Math.max(queue.length - 1, 0);

	let selectedAnswer: string | null = null;
	let isOther = false;
	let otherText = '';
	// A fresh current question resets any stale selection from the last one.
	$: current, ((selectedAnswer = null), (isOther = false), (otherText = ''));

	$: answerText = isOther ? otherText.trim() : (selectedAnswer ?? '');
	$: canAnswer = !!current && answerText.length > 0;

	function selectOption(option: string) {
		selectedAnswer = option;
		isOther = false;
	}

	function selectOther() {
		selectedAnswer = null;
		isOther = true;
		tick().then(() => otherInputEl?.focus());
	}

	async function answer() {
		if (!current || !canAnswer) return;
		const quoted = composeQuestionAnswer(current.text, answerText);
		dismissedQuestions.dismiss(current.messageId, current.index);
		await onAnswerQuestion?.(quoted);
	}

	function skip() {
		if (!current) return;
		dismissedQuestions.dismiss(current.messageId, current.index);
	}

	function skipAll() {
		for (const entry of queue) {
			dismissedQuestions.dismiss(entry.messageId, entry.index);
		}
	}

	function otherKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter' && canAnswer) answer();
	}
</script>

{#if current}
	<div class="question-dock-wrap">
		<section class="question-dock" aria-labelledby="questionText">
			<header class="question-head">
				<span class="question-indicator"><svg class="icon"><use href="#i-info" /></svg></span>
				<strong>PotionAI needs your input</strong>
				<span class="question-progress">Question {current.index + 1} of {current.total}</span>
			</header>
			<div class="question-body">
				<p class="question-text" id="questionText">{@html processMarkdown(current.text)}</p>
				<p class="question-context">Choose one answer. It will be sent as your next reply.</p>
				<div class="question-options">
					{#each current.options as option (option)}
						<button
							class="question-option"
							class:is-selected={!isOther && selectedAnswer === option}
							type="button"
							on:click={() => selectOption(option)}
						>
							<span class="question-radio"></span><span>{option}</span>
						</button>
					{/each}
					<button class="question-option" class:is-selected={isOther} type="button" on:click={selectOther}>
						<span class="question-radio"></span><span>Something else…</span>
					</button>
				</div>
				<div class="question-other-field" class:hidden={!isOther}>
					<input
						bind:this={otherInputEl}
						bind:value={otherText}
						type="text"
						placeholder="Type a different answer…"
						on:keydown={otherKeydown}
					/>
				</div>
			</div>
			<footer class="question-footer">
				<span class="question-queued">
					{remaining > 0 ? `${remaining} more question${remaining === 1 ? '' : 's'} queued` : 'Last question'}
				</span>
				{#if remaining > 0}
					<button class="question-secondary" type="button" on:click={skipAll}>Skip all</button>
				{/if}
				<button class="question-secondary" type="button" on:click={skip}>Skip</button>
				<button class="question-answer" type="button" disabled={!canAnswer} on:click={answer}>Answer</button>
			</footer>
		</section>
	</div>
{/if}
