export interface Document {
  id: string;
  filename: string;
  file_size: number;
  file_type: string;
  status: string;
  chunk_count: number;
  error_message?: string;
  created_at: string;
}

export interface Chunk {
  chunk_id: string;
  text: string;
}

export interface DocumentChunks {
  document_id: string;
  filename: string;
  chunks: Chunk[];
}
