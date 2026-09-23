import type { CitySuggestion, HubSuggestion } from "../../lib/discovery/types";

export const discoveryCities: CitySuggestion[] = [
  {
    id: "00000000-0000-0000-0000-000000000001",
    name: "成都",
    nameEn: "Chengdu",
    provinceName: "四川省",
    adcode: "510100",
  },
  {
    id: "00000000-0000-0000-0000-000000000002",
    name: "乐山",
    nameEn: "Leshan",
    provinceName: "四川省",
    adcode: "511100",
  },
  {
    id: "00000000-0000-0000-0000-000000000003",
    name: "南京",
    nameEn: "Nanjing",
    provinceName: "江苏省",
    adcode: "320100",
  },
];

export const chengduHubs: HubSuggestion[] = [
  {
    id: "10000000-0000-0000-0000-000000000001",
    name: "成都天府国际机场",
    nameEn: "Chengdu Tianfu International Airport",
    type: "AIRPORT",
    importanceLevel: 5,
    active: true,
    passengerService: true,
    aliases: ["成都天府机场"],
  },
  {
    id: "10000000-0000-0000-0000-000000000002",
    name: "成都东站",
    nameEn: "Chengdu East Railway Station",
    type: "RAILWAY",
    importanceLevel: 5,
    active: true,
    passengerService: true,
    aliases: ["成都东"],
  },
  {
    id: "10000000-0000-0000-0000-000000000003",
    name: "成都南站",
    nameEn: "Chengdu South Railway Station",
    type: "RAILWAY",
    importanceLevel: 4,
    active: true,
    passengerService: true,
    aliases: ["成都南"],
  },
  {
    id: "10000000-0000-0000-0000-000000000004",
    name: "成都西站",
    nameEn: "Chengdu West Railway Station",
    type: "RAILWAY",
    importanceLevel: 3,
    active: true,
    passengerService: true,
    aliases: ["成都西"],
  },
  {
    id: "10000000-0000-0000-0000-000000000005",
    name: "成都货运站",
    nameEn: null,
    type: "RAILWAY",
    importanceLevel: 1,
    active: true,
    passengerService: false,
    aliases: [],
  },
  {
    id: "10000000-0000-0000-0000-000000000006",
    name: "旧成都机场",
    nameEn: null,
    type: "AIRPORT",
    importanceLevel: 1,
    active: false,
    passengerService: true,
    aliases: [],
  },
];

export const leshanHubs: HubSuggestion[] = [
  {
    id: "20000000-0000-0000-0000-000000000001",
    name: "乐山站",
    nameEn: "Leshan Railway Station",
    type: "RAILWAY",
    importanceLevel: 5,
    active: true,
    passengerService: true,
    aliases: ["乐山火车站"],
  },
  {
    id: "20000000-0000-0000-0000-000000000002",
    name: "乐山北站",
    nameEn: "Leshan North Railway Station",
    type: "RAILWAY",
    importanceLevel: 3,
    active: true,
    passengerService: true,
    aliases: ["乐山北"],
  },
];

export function discoveryHubsForCity(cityId: string): HubSuggestion[] {
  if (cityId === discoveryCities[0].id) return chengduHubs;
  if (cityId === discoveryCities[1].id) return leshanHubs;
  return [];
}
