/**
 * Audio generation types for audio generation features (Maya TTS)
 */

export type AudioTrackType = 'vocal' | 'instrumental' | 'mixed' | 'speech';

export interface AudioTrack {
	type: AudioTrackType;
	url: string;
	originalUrl?: string;
	duration?: number;
	sample_rate?: number;
	channels?: number;
	file_size?: number;
	format?: string; // mp3, wav, flac, etc.
}

export interface AudioOutput {
	id?: string;
	generation_id?: string;
	tracks: AudioTrack[];
	metadata?: {
		prompt?: string;
		model?: string;
		duration?: number;
		sample_rate?: number;
		created_at?: string;
	};
}

export interface AudioData {
	url: string;
	originalUrl?: string;
	track_type?: AudioTrackType;
	duration?: number;
	sample_rate?: number;
	channels?: number;
	file_size?: number;
	format?: string;
	seed?: number;
	file_type?: 'audio';
	/** Produced from another final file of this generation (e.g. an enhance pass);
	 *  see the matching field on ImageData/VideoData/MeshData and `leadFile.ts`. */
	derived?: boolean;
}

export interface AudioPlayerState {
	isPlaying: boolean;
	currentTime: number;
	duration: number;
	volume: number;
	selectedTrack: AudioTrackType;
	isLoading: boolean;
	error?: string;
}

export interface WaveformConfig {
	height: number;
	waveColor: string;
	progressColor: string;
	cursorColor: string;
	backgroundColor: string;
	barWidth?: number;
	barGap?: number;
	barRadius?: number;
	responsive?: boolean;
}
