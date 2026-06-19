-- Migration: add per-platform video IDs to clips table
-- Supports TikTok, Instagram Reels, and Facebook Reels alongside youtube_id.
-- Each column is nullable — populated only after a successful upload to that platform.

ALTER TABLE clips ADD COLUMN tiktok_id TEXT;
ALTER TABLE clips ADD COLUMN instagram_id TEXT;
ALTER TABLE clips ADD COLUMN facebook_id TEXT;
