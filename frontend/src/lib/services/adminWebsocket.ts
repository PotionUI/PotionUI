/**
 * Admin WebSocket Service
 *
 * Manages WebSocket connection for admin panel real-time updates.
 * Separate from generation WebSocket to allow independent lifecycle management.
 */

import { writable, type Writable } from 'svelte/store';
import { logger } from '$lib/utils/logger';
import type { BaseWebSocketMessage } from './BaseWebSocket';
import { StatefulWebSocket, type ConnectionState } from './StatefulWebSocket';
import type { ProvisionedCompute } from '$lib/services/admin-api';
import type { IndexingStatus } from '$lib/services/api/models';

export type { ConnectionState };

const adminConnectionState: Writable<ConnectionState> = writable('disconnected');

// Progress of a pipe requirement install (pip/git), which runs far too long
// for the POST that started it to carry its outcome.
export interface PipeInstallStatus {
	pipe: string;
	status: 'installing' | 'installed' | 'error';
	message: string | null;
}

// A provisioned-compute row changed - every progress step, every state
// change, every refresh/stop. Always the whole row; callers replace, never merge.
export interface ComputeStatusMessage {
	row: ProvisionedCompute;
}

type PipeInstallCallback = (status: PipeInstallStatus) => void;
type ComputeStatusCallback = (status: ComputeStatusMessage) => void;
type IndexingStatusCallback = (status: IndexingStatus) => void;
type ConnectedCallback = () => void;

class AdminWebSocketService extends StatefulWebSocket {
	private pipeInstallCallbacks: Set<PipeInstallCallback> = new Set();
	private computeStatusCallbacks: Set<ComputeStatusCallback> = new Set();
	private indexingStatusCallbacks: Set<IndexingStatusCallback> = new Set();
	private connectedCallbacks: Set<ConnectedCallback> = new Set();

	constructor() {
		super(adminConnectionState, '/ws/admin', 'admin');
	}

	protected override onMessage(message: BaseWebSocketMessage): void {
		switch (message.type) {
			case 'connection_established':
				this.connectedCallbacks.forEach((cb) => cb());
				break;

			case 'heartbeat':
			case 'pong':
				break;

			case 'pipe_install_status':
				this.pipeInstallCallbacks.forEach((cb) =>
					cb({
						pipe: message.pipe as string,
						status: message.status as PipeInstallStatus['status'],
						message: (message.message as string | null) ?? null
					})
				);
				break;

			case 'compute_status':
				this.computeStatusCallbacks.forEach((cb) => cb({ row: message.row as ProvisionedCompute }));
				break;

			case 'indexing_status':
				this.indexingStatusCallbacks.forEach((cb) => cb(message.status as IndexingStatus));
				break;

			default:
				logger.debug('Unknown admin message type:', message.type);
		}
	}

	onPipeInstallStatus(callback: PipeInstallCallback): () => void {
		this.pipeInstallCallbacks.add(callback);
		return () => this.pipeInstallCallbacks.delete(callback);
	}

	onComputeStatus(callback: ComputeStatusCallback): () => void {
		this.computeStatusCallbacks.add(callback);
		return () => this.computeStatusCallbacks.delete(callback);
	}

	onIndexingStatus(callback: IndexingStatusCallback): () => void {
		this.indexingStatusCallbacks.add(callback);
		return () => this.indexingStatusCallbacks.delete(callback);
	}

	onConnected(callback: ConnectedCallback): () => void {
		this.connectedCallbacks.add(callback);
		return () => this.connectedCallbacks.delete(callback);
	}
}

export const adminWebSocket = new AdminWebSocketService();
