import { afterEach, describe, expect, it } from 'vitest';
import { get } from 'svelte/store';
import { collectProvidedContext, declaredMode, resetPageContext } from '$lib/chat/pageContext';
import { bindElixirChat } from './chat';

afterEach(() => resetPageContext());

describe('bindElixirChat', () => {
	it('makes the chat the Elixirs assistant for the open elixir while the tab is shown', () => {
		const release = bindElixirChat('x1');

		expect(get(declaredMode)).toBe('elixirs');
		expect(collectProvidedContext()).toEqual({ elixir: { elixir_id: 'x1' } });

		release();

		expect(get(declaredMode)).toBeNull();
		expect(collectProvidedContext()).toEqual({});
	});

	it('follows the elixir the tab switches to', () => {
		const first = bindElixirChat('x1');
		first();
		const second = bindElixirChat('x2');

		expect(collectProvidedContext()).toEqual({ elixir: { elixir_id: 'x2' } });
		second();
	});
});
