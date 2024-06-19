const express = require("express");
const app = express();
const cors = require("cors");

app.use(cors());
const port = 3001;

const API_Key = "frostburg_cs";

app.use((req, res, next) => {
  const apiKey = req.query.api_key;
  if (apiKey != API_Key) {
    return res.status(401).json({ error: "Invalid Key" });
  }
  next();
});

app.get("/api/sewer-population", (req, res) => {
  const data = {
    sewerData: [
      {
        total_population: 11000,
        sewer_population: 5000,
        sewer_flow: 1500.5,
        sewer1_population: 1628701,
        sewer1_flow: 456.2,
        sewer2_population: 2635000,
        sewer2_flow: 712.4,
        sewer3_population: 2298000,
        sewer3_flow: 598.7,
        sewer4_population: 51000,
        sewer4_flow: 14.6,
        sewer5_population: 198000,
        sewer5_flow: 52.1,
        sewer6_population: 89000,
        sewer6_flow: 23.8,
      },
    ],
  };
  res.json(data);
});

app.get("/api/weather", (req, res) => {
  const data = {
    regions: [
      {
        region: "Frostburg",
        date1: "2024-06-12",
        temp1: 72,
        humidity1: 65,
        precip1: 0.2,
        date2: "2024-06-13",
        temp2: 75,
        humidity2: 70,
        precip2: 0.1,
        date3: "2024-06-14",
        temp3: 78,
        humidity3: 62,
        precip3: 0,
        date4: "2024-06-12",
        temp4: 95,
        humidity4: 20,
        precip4: 0,
        date5: "2024-06-13",
        temp5: 98,
        humidity5: 15,
        precip5: 0,
        date6: "2024-06-14",
        temp6: 102,
        humidity6: 10,
        precip6: 0,
      },
    ],
  };
  res.json(data);
});

app.get("/api/temperature", (req, res) => {
  const data = {
    cities: [
      {
        city: "General",
        date1: "2024-06-12",
        min_temp1: 65,
        max_temp1: 78,
        date2: "2024-06-13",
        min_temp2: 68,
        max_temp2: 82,
        date3: "2024-06-14",
        min_temp3: 70,
        max_temp3: 85,
        date4: "2024-06-12",
        min_temp4: 75,
        max_temp4: 88,
        date5: "2024-06-13",
        min_temp5: 77,
        max_temp5: 90,
        date6: "2024-06-14",
        min_temp6: 79,
        max_temp6: 92,
      },
    ],
  };
  res.json(data);
});

app.get("/api/air-quality", (req, res) => {
  const data = {
    cities: [
      {
        city: "General",
        date1: "2024-06-12",
        pm25_1: 85,
        pm10_1: 120,
        no2_1: 45,
        so2_1: 20,
        date2: "2024-06-13",
        pm25_2: 90,
        pm10_2: 125,
        no2_2: 50,
        so2_2: 18,
        date3: "2024-06-14",
        pm25_3: 92,
        pm10_3: 130,
        no2_3: 55,
        so2_3: 22,
        date4: "2024-06-12",
        pm25_4: 12,
        pm10_4: 25,
        no2_4: 18,
        so2_4: 5,
        date5: "2024-06-13",
        pm25_5: 10,
        pm10_5: 22,
        no2_5: 15,
        so2_5: 4,
        date6: "2024-06-14",
        pm25_6: 8,
        pm10_6: 20,
        no2_6: 12,
        so2_6: 3,
      },
    ],
  };
  res.json(data);
});

app.get("/api/sustainability-index", (req, res) => {
  const data = {
    countries: [
      {
        country1: "Norway",
        sustainability_index1: 92.7,
        env_quality1: 95.2,
        renewable_energy1: 88.4,
        waste_management1: 94.1,
        country2: "Brazil",
        sustainability_index2: 68.5,
        env_quality2: 72.8,
        renewable_energy2: 65.2,
        waste_management2: 58.7,
      },
    ],
  };
  res.json(data);
});

app.get("/api/water-quality", (req, res) => {
  const data = {
    locations: [
      {
        location: "General",
        date1: "2024-06-12",
        ph1: 7.2,
        nitrate1: 3.5,
        lead1: 0.01,
        mercury1: 0.002,
        date2: "2024-06-13",
        ph2: 7.1,
        nitrate2: 3.6,
        lead2: 0.009,
        mercury2: 0.002,
        date3: "2024-06-12",
        ph3: 6.8,
        nitrate3: 4.1,
        lead3: 0.015,
        mercury3: 0.003,
        date4: "2024-06-13",
        ph4: 6.9,
        nitrate4: 4.0,
        lead4: 0.014,
        mercury4: 0.003,
      },
    ],
  };
  res.json(data);
});

app.get("/api/outbreaks", (req, res) => {
  const data = {
    regions: [
      {
        region: "General",
        disease1: "Influenza",
        cases1: 12000,
        deaths1: 150,
        date1: "2024-06-12",
        disease2: "COVID-19",
        cases2: 5000,
        deaths2: 50,
        date2: "2024-06-13",
        disease3: "Measles",
        cases3: 300,
        deaths3: 5,
        date3: "2024-06-12",
        disease4: "Influenza",
        cases4: 8000,
        deaths4: 100,
        date4: "2024-06-13",
      },
    ],
  };
  res.json(data);
});

app.get("/api/energy-consumption", (req, res) => {
  const data = {
    sectors: [
      {
        sector: "General",
        date1: "2024-06-12",
        electricity1: 1200,
        natural_gas1: 500,
        renewables1: 300,
        date2: "2024-06-13",
        electricity2: 1250,
        natural_gas2: 520,
        renewables2: 310,
        date3: "2024-06-12",
        electricity3: 3000,
        natural_gas3: 1500,
        renewables3: 800,
        date4: "2024-06-13",
        electricity4: 3100,
        natural_gas4: 1550,
        renewables4: 820,
      },
    ],
  };
  res.json(data);
});

app.get("/api/waste-management", (req, res) => {
  const data = {
    cities: [
      {
        city: "General",
        date1: "2024-06-12",
        recycled1: 500,
        landfilled1: 300,
        composted1: 200,
        date2: "2024-06-13",
        recycled2: 520,
        landfilled2: 310,
        composted2: 210,
        date3: "2024-06-12",
        recycled3: 700,
        landfilled3: 400,
        composted3: 300,
        date4: "2024-06-13",
        recycled4: 720,
        landfilled4: 410,
        composted4: 310,
      },
    ],
  };
  return res.json(data);
});

app.get("/api/air-quality-index", (req, res) => {
  const data = {
    cities: [
      {
        city: "General",
        date1: "2024-06-12",
        aqi1: 75,
        pm25_1: 35,
        pm10_1: 50,
        ozone1: 20,
        date2: "2024-06-13",
        aqi2: 80,
        pm25_2: 38,
        pm10_2: 52,
        ozone2: 22,
        date3: "2024-06-12",
        aqi3: 60,
        pm25_3: 25,
        pm10_3: 40,
        ozone3: 15,
        date4: "2024-06-13",
        aqi4: 65,
        pm25_4: 28,
        pm10_4: 42,
        ozone4: 18,
      },
    ],
  };
  res.json(data);
});

app.get("/api/Frostburg", (req, res) => {
  const data = {
    university: "Frostburg State University",
    sustainability: {
      energyConsumption: {
        buildings: [
          {
            name: "Compton Science Center",
            data: [
              {
                date: "2024-06-12",
                electricity: 1500,
                naturalGas: 800,
                renewables: 200,
              },
              {
                date: "2024-06-13",
                electricity: 1520,
                naturalGas: 810,
                renewables: 210,
              },
            ],
          },
          {
            name: "Gira Center",
            data: [
              {
                date: "2024-06-12",
                electricity: 1200,
                naturalGas: 600,
                renewables: 150,
              },
              {
                date: "2024-06-13",
                electricity: 1250,
                naturalGas: 620,
                renewables: 160,
              },
            ],
          },
        ],
      },
      wasteManagement: {
        buildings: [
          {
            name: "Lane University Center",
            data: [
              {
                date: "2024-06-12",
                recycled: 300,
                landfilled: 200,
                composted: 100,
              },
              {
                date: "2024-06-13",
                recycled: 320,
                landfilled: 210,
                composted: 110,
              },
            ],
          },
          {
            name: "Cambridge Hall",
            data: [
              {
                date: "2024-06-12",
                recycled: 250,
                landfilled: 150,
                composted: 80,
              },
              {
                date: "2024-06-13",
                recycled: 260,
                landfilled: 160,
                composted: 90,
              },
            ],
          },
        ],
      },
      waterUsage: {
        buildings: [
          {
            name: "Cordts PE Center",
            data: [
              {
                date: "2024-06-12",
                waterUsed: 5000,
                waterRecycled: 1000,
              },
              {
                date: "2024-06-13",
                waterUsed: 5200,
                waterRecycled: 1100,
              },
            ],
          },
          {
            name: "Dunkle Hall",
            data: [
              {
                date: "2024-06-12",
                waterUsed: 3000,
                waterRecycled: 800,
              },
              {
                date: "2024-06-13",
                waterUsed: 3100,
                waterRecycled: 850,
              },
            ],
          },
        ],
      },
      airQuality: {
        locations: [
          {
            name: "Upper Quad",
            data: [
              {
                date: "2024-06-12",
                pm25: 12,
                pm10: 20,
                ozone: 15,
              },
              {
                date: "2024-06-13",
                pm25: 14,
                pm10: 22,
                ozone: 16,
              },
            ],
          },
          {
            name: "Lower Quad",
            data: [
              {
                date: "2024-06-12",
                pm25: 10,
                pm10: 18,
                ozone: 14,
              },
              {
                date: "2024-06-13",
                pm25: 11,
                pm10: 19,
                ozone: 15,
              },
            ],
          },
        ],
      },
    },
  };
  res.json(data);
});

app.listen(port, () => {
  console.log(`Server is listening on http://localhost:${port}
    httpLink: http://localhost:3001/api/Frostburg?api_key={API_KEY_HERE}
    `);
});
