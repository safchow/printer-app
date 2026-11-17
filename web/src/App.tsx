import { BrowserRouter, Route, Routes } from "react-router-dom";
import { TooltipProvider } from "@/components/ui/tooltip";
import { PrintersProvider } from "@/hooks/usePrinters";
import { ThemeProvider } from "@/hooks/useTheme";
import { Dashboard } from "@/pages/Dashboard";
import { PrinterPage } from "@/pages/PrinterPage";

export default function App() {
  return (
    <ThemeProvider>
      <PrintersProvider>
        <TooltipProvider delayDuration={200}>
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/printers/:id" element={<PrinterPage />} />
              <Route path="*" element={<Dashboard />} />
            </Routes>
          </BrowserRouter>
        </TooltipProvider>
      </PrintersProvider>
    </ThemeProvider>
  );
}
