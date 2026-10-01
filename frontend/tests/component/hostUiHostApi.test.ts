import { describe, it, expect } from 'vitest';
import { initHostApi } from '$lib/plugin-api/host';
import { confirmDialog } from '$lib/stores/confirm';

describe('initHostApi', () => {
	it('exposes confirm and the host ui components', () => {
		initHostApi();
		const host = (window as any).__potionui;
		expect(host.confirm).toBe(confirmDialog);
		expect(typeof host.components.Button.mount).toBe('function');
	});
});
