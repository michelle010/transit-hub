export type HubSuggestionType = "AIRPORT" | "RAILWAY";

export interface CitySuggestion {
  id: string;
  name: string;
  nameEn: string | null;
  provinceName: string;
  adcode: string | null;
}

export interface HubSuggestion {
  id: string;
  name: string;
  nameEn: string | null;
  type: HubSuggestionType;
  importanceLevel: number;
  active: boolean;
  passengerService: boolean;
  aliases: string[];
}

export interface DiscoveryOption {
  id: string;
  label: string;
  meta?: string;
}
