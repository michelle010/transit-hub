import { describe, expect, it } from "vitest";

import { filterHubSuggestions } from "./useDiscoverySuggestions";
import type { HubSuggestion } from "./types";

const hubs: HubSuggestion[] = [
  {
    id: "airport",
    name: "成都天府国际机场",
    nameEn: null,
    type: "AIRPORT",
    importanceLevel: 5,
    active: true,
    passengerService: true,
    aliases: ["成都天府机场"],
  },
  {
    id: "east",
    name: "成都东站",
    nameEn: "Chengdu East",
    type: "RAILWAY",
    importanceLevel: 5,
    active: true,
    passengerService: true,
    aliases: ["成都东"],
  },
  {
    id: "south",
    name: "成都南站",
    nameEn: null,
    type: "RAILWAY",
    importanceLevel: 4,
    active: true,
    passengerService: true,
    aliases: ["成都南"],
  },
];

describe("hub suggestion filtering", () => {
  it("matches canonical names, aliases and English names within the current city", () => {
    expect(filterHubSuggestions("天府机场", hubs).map((hub) => hub.id)).toEqual(["airport"]);
    expect(filterHubSuggestions("成都东", hubs).map((hub) => hub.id)).toEqual(["east"]);
    expect(filterHubSuggestions("east", hubs).map((hub) => hub.id)).toEqual(["east"]);
  });

  it("limits contextual suggestions and does not mutate the source array", () => {
    const manyHubs = Array.from({ length: 12 }, (_, index) => ({
      ...hubs[0],
      id: `hub-${index}`,
    }));
    const result = filterHubSuggestions("", manyHubs);
    expect(result).toHaveLength(8);
    expect(manyHubs).toHaveLength(12);
  });
});
