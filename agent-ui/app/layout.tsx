import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HomeFix Assistant",
  description: "Chat with the HomeFix agent hosted on Databricks",
};

const RootLayout = ({ children }: { children: React.ReactNode }) => (
  <html lang="en">
    <body>{children}</body>
  </html>
);

export default RootLayout;
