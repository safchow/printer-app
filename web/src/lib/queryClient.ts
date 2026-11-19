import { QueryClient } from "@tanstack/react-query";

/**
 * One cache for the whole app. Files, labels and history are cached for
 * 30 s ("fresh": switching pages shows them with no request at all); after
 * that a revisit still shows the cached copy at once and refreshes it in the
 * background. Live printer status doesn't go through here: it comes over the
 * WebSocket in PrintersProvider.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 30 * 60_000,
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
});
