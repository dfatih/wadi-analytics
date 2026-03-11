# Graph Schema Diagram -- Neo4j Knowledge Graph

Shows the node labels, their properties, relationships, and index types
in the Neo4j graph database.

```mermaid
erDiagram
    Site {
        string SiteID PK
        string Category
        string Location1
        string Location2
        string Surface
        int NoOfFeatures
        float X
        float Y
        float Lon
        float Lat
        float Shape_Length
        float Shape_Area
        point location
        vector embedding
    }

    Feature {
        string FeatureID PK
        string Category
        string Category2
        int Age
        string Condition
        float Length
        float Width
        float Height
        string Location1
        string Location2
        float X
        float Y
        float Lon
        float Lat
        point location
        vector embedding
    }

    RockArtMotif {
        string name PK
    }

    Site ||--o{ Feature : HAS_FEATURE
    Feature }o--o{ RockArtMotif : "HAS_ROCKART {position}"
    Site }o--o{ Site : "CLOSE_TO_SITE {distance_m}"
    Feature }o--o{ Feature : "CLOSE_TO_FEATURE {distance_m}"
    Feature }o--o{ Site : "NEAR {distance_m}"
```

## Relationship Semantics

| Relationship | Direction | Condition | Properties |
|-------------|-----------|-----------|------------|
| HAS_FEATURE | Site to Feature | Parent-child | none |
| HAS_ROCKART | Feature to RockArtMotif | Motif present | `position` (int, 1--6) |
| CLOSE_TO_SITE | Site -- Site | distance < 500 m | `distance_m` (float) |
| CLOSE_TO_FEATURE | Feature -- Feature | distance < 500 m | `distance_m` (float) |
| NEAR | Feature -- Site | distance < 500 m, non-parent | `distance_m` (float) |

## Index Types

| Index | Node | Properties | Type |
|-------|------|-----------|------|
| Uniqueness | Site | SiteID | Constraint |
| Uniqueness | Feature | FeatureID | Constraint |
| Uniqueness | RockArtMotif | name | Constraint |
| Property | Feature | Category, Location1, Age | B-tree |
| Property | Site | Category, Location1 | B-tree |
| Point | Site, Feature | location | Spatial (WGS-84) |
| Vector | Site, Feature | embedding | Vector (1536-dim, cosine) |
| Fulltext | Site | Category, Surface | Fulltext |
| Fulltext | Feature | Category, Category2, Condition | Fulltext |

## Cardinalities (from live database)

- Sites: 8,362 nodes
- Features: 16,807 nodes
- RockArtMotifs: 7 nodes
- HAS_FEATURE edges: 16,807
- HAS_ROCKART edges: 16
- CLOSE_TO_SITE edges: 90,101
- CLOSE_TO_FEATURE edges: 426,792
- NEAR edges: 350,091
