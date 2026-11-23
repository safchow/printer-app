import { QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LibrarySync } from "@/hooks/useLibrarySync";
import { PrintersProvider } from "@/hooks/usePrinters";
import { ThemeProvider } from "@/hooks/useTheme";
import { Dashboard } from "@/pages/Dashboard";
import { FilesPage } from "@/pages/FilesPage";
import { HistoryEntryPage } from "@/pages/HistoryEntryPage";
import { HistoryPage } from "@/pages/HistoryPage";
import { PrinterPage } from "@/pages/PrinterPage";
import { QueuePage } from "@/pages/QueuePage";
import { queryClient } from "@/lib/queryClient";

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <PrintersProvider>
          <LibrarySync />
          <TooltipProvider delayDuration={200}>
            <BrowserRouter>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/printers/:id" element={<PrinterPage />} />
                <Route path="/files" element={<FilesPage />} />
                <Route path="/queue" element={<QueuePage />} />
                <Route path="/history" element={<HistoryPage />} />
                <Route path="/history/:id" element={<HistoryEntryPage />} />
                <Route path="*" element={<Dashboard />} />
              </Routes>
            </BrowserRouter>
          </TooltipProvider>
        </PrintersProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
