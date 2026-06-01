import type { FrontendPayload } from '../types';
import { getLoadingStatusText } from './loadingStatus';

export function getAssistantDisplayText(
  payload: FrontendPayload,
  options: { loading?: boolean; liveThinking?: string } = {},
): string {
  if (payload.type === 'error') {
    return payload.message || '服务暂时不可用，请稍后重试。';
  }

  if (payload.message) {
    return payload.message;
  }

  if (options.loading) {
    return getLoadingStatusText(options.liveThinking);
  }

  return '';
}
