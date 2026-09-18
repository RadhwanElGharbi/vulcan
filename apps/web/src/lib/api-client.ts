/**
 * API Client for AGRS ZEUS Backend
 */

function normalizeApiBaseUrl(rawBaseUrl: string): string {
  const trimmed = (rawBaseUrl || '').trim().replace(/\/+$/, '')
  if (!trimmed) return '/api'

  // Backend mounts routers under /api (see apps/api/main.py). Make this resilient to envs that
  // set NEXT_PUBLIC_API_URL to the host only (e.g. http://localhost:8000).
  if (trimmed.endsWith('/api')) return trimmed
  return `${trimmed}/api`
}

export const API_BASE_URL = normalizeApiBaseUrl(process.env.NEXT_PUBLIC_API_URL || '/api')

export interface HealthResponse {
  status: string;
  timestamp: string;
  version: string;
  services: Record<string, string>;
}

export interface ProjectInfo {
  id: string;
  name: string;
  description: string;
  created_at: string;
  status: string;
}

export interface ConfigResponse {
  mapbox_token: string;
  api_version: string;
  features: string[];
}

class ApiClient {
  private baseUrl: string;

  constructor(baseUrl: string = API_BASE_URL) {
    this.baseUrl = baseUrl;
  }

  private async request<T>(endpoint: string, options?: RequestInit): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`;
    
    try {
      const response = await fetch(url, {
        ...options,
        headers: {
          'Content-Type': 'application/json',
          ...options?.headers,
        },
      });

      if (!response.ok) {
        throw new Error(`API Error: ${response.status} ${response.statusText}`);
      }

      return await response.json();
    } catch (error) {
      console.error(`API Request failed: ${url}`, error);
      throw error;
    }
  }

  // Health check
  async health(): Promise<HealthResponse> {
    return this.request<HealthResponse>('/health');
  }

  // Get all projects
  async getProjects(): Promise<ProjectInfo[]> {
    return this.request<ProjectInfo[]>('/projects');
  }

  // Get project details
  async getProjectDetails(projectId: string): Promise<any> {
    return this.request(`/projects/${projectId}`);
  }

  // Get configuration
  async getConfig(): Promise<ConfigResponse> {
    return this.request<ConfigResponse>('/config');
  }
}

// Export singleton instance
export const apiClient = new ApiClient();








