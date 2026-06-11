from azure.storage.blob import BlobServiceClient
import pandas as pd
import io
import json
import os
from datetime import datetime

def process_nutritional_data_from_azurite():
    print(f"Function started at: {datetime.now()}")

    # Connect to Azurite using built-in development storage shortcut
    connect_str = "UseDevelopmentStorage=true"

    print("Connecting to Azurite Blob Storage...")
    blob_service_client = BlobServiceClient.from_connection_string(connect_str)

    container_name = "datasets"
    blob_name = "All_Diets.csv"

    # Download the CSV from Azurite
    print(f"Downloading {blob_name} from container '{container_name}'...")
    container_client = blob_service_client.get_container_client(container_name)
    blob_client = container_client.get_blob_client(blob_name)

    stream = blob_client.download_blob().readall()
    df = pd.read_csv(io.BytesIO(stream))
    print(f"Successfully loaded {len(df)} recipes from Azurite Blob Storage")

    # ─────────────────────────────────────────
    # CLEAN DATA
    # ─────────────────────────────────────────
    print("\nCleaning data...")
    df['Protein(g)'] = pd.to_numeric(df['Protein(g)'], errors='coerce')
    df['Carbs(g)']   = pd.to_numeric(df['Carbs(g)'],   errors='coerce')
    df['Fat(g)']     = pd.to_numeric(df['Fat(g)'],     errors='coerce')
    df.fillna(df.mean(numeric_only=True), inplace=True)
    df['Diet_type'] = df['Diet_type'].str.strip().str.capitalize()
    print("Data cleaned successfully")

    # ─────────────────────────────────────────
    # ANALYSIS 1: Average macros per diet type
    # ─────────────────────────────────────────
    print("\n=== Average Macronutrients by Diet Type ===")
    avg_macros = df.groupby('Diet_type')[['Protein(g)', 'Carbs(g)', 'Fat(g)']].mean().round(2)
    print(avg_macros)

    # ─────────────────────────────────────────
    # ANALYSIS 2: Top 5 protein recipes per diet
    # ─────────────────────────────────────────
    print("\n=== Top 5 Protein-Rich Recipes Per Diet Type ===")
    top_protein = (
        df.sort_values('Protein(g)', ascending=False)
          .groupby('Diet_type')
          .head(5)[['Diet_type', 'Recipe_name', 'Protein(g)']]
    )
    print(top_protein.to_string(index=False))

    # ─────────────────────────────────────────
    # ANALYSIS 3: Highest protein diet
    # ─────────────────────────────────────────
    highest_protein_diet  = avg_macros['Protein(g)'].idxmax()
    highest_protein_value = avg_macros['Protein(g)'].max()
    print(f"\n=== Diet with Highest Average Protein ===")
    print(f"{highest_protein_diet}: {highest_protein_value:.2f}g")

    # ─────────────────────────────────────────
    # ANALYSIS 4: Most common cuisine per diet
    # ─────────────────────────────────────────
    print("\n=== Most Common Cuisine Per Diet Type ===")
    most_common_cuisine = df.groupby('Diet_type')['Cuisine_type'].agg(
        lambda x: x.value_counts().idxmax()
    )
    print(most_common_cuisine)

    # ─────────────────────────────────────────
    # ANALYSIS 5: New metrics
    # ─────────────────────────────────────────
    df['Protein_to_Carbs_ratio'] = (df['Protein(g)'] / df['Carbs(g)'].replace(0, float('nan'))).round(4)
    df['Carbs_to_Fat_ratio']     = (df['Carbs(g)']   / df['Fat(g)'].replace(0, float('nan'))).round(4)
    print("\n=== Sample New Metrics (Protein-to-Carbs & Carbs-to-Fat Ratios) ===")
    print(df[['Recipe_name', 'Protein_to_Carbs_ratio', 'Carbs_to_Fat_ratio']].head(8).to_string(index=False))

    # Calculate average ratios per diet type for JSON output
    avg_ratios = df.groupby('Diet_type')[['Protein_to_Carbs_ratio', 'Carbs_to_Fat_ratio']].mean().round(4)
    print("\n=== Average Ratios by Diet Type ===")
    print(avg_ratios)

    # ─────────────────────────────────────────
    # SAVE RESULTS TO JSON 
    # ─────────────────────────────────────────
    os.makedirs('simulated_nosql', exist_ok=True)

    result = {
        "processed_at": str(datetime.now()),
        "total_recipes": len(df),
        "highest_protein_diet": {
            "diet_type": highest_protein_diet,
            "avg_protein_g": highest_protein_value
        },
        "most_common_cuisine_per_diet": most_common_cuisine.to_dict(),
        "avg_macros_by_diet": avg_macros.reset_index().to_dict(orient='records'),
        "top_5_protein_recipes": top_protein.to_dict(orient='records'),
        "avg_ratios_by_diet": avg_ratios.reset_index().to_dict(orient='records')
    }

    output_path = 'simulated_nosql/results.json'
    with open(output_path, 'w') as f:
        json.dump(result, f, indent=4)

    print(f"\nResults saved to {output_path}")
    print(f"Function completed at: {datetime.now()}")
    return "Data processed and stored successfully."

# Run the function
print(process_nutritional_data_from_azurite())