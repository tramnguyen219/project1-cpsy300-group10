import azure.functions as func
import logging
import os
import io
import json
import time
import pandas as pd
from datetime import datetime
from azure.storage.blob import BlobServiceClient
from auth_routes import bp as auth_bp
from oauth_routes import bp as oauth_bp

app = func.FunctionApp()
app.register_blueprint(auth_bp)
app.register_blueprint(oauth_bp)

CONTAINER = "diets-dataset"   
BLOB_NAME = "All_Diets.csv"
CLEANED_BLOB_NAME = "cleaned_diets.csv"
RESULT_BLOB_NAME = "dashboard_results.json"

@app.route(route="GetNutritionalInsights", auth_level=func.AuthLevel.ANONYMOUS)
def GetNutritionalInsights(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("GetNutritionalInsights function triggered.")
    start_time = time.time()

    try:
        
        diet_filter = req.params.get("diet_type")

        conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
        client = BlobServiceClient.from_connection_string(conn_str)
        blob_client = client.get_blob_client(container=CONTAINER, blob=BLOB_NAME)

        # Read CSV directly from Azure Blob Storage
        stream = blob_client.download_blob().readall()
        df = pd.read_csv(io.BytesIO(stream))

        numeric_cols = ['Protein(g)', 'Carbs(g)', 'Fat(g)']
        df[numeric_cols] = df[numeric_cols].fillna(df[numeric_cols].mean())

        if diet_filter:
            df = df[df['Diet_type'].str.lower() == diet_filter.lower()]
            if df.empty:
                return func.HttpResponse(
                    json.dumps({"error": f"No data found for diet_type '{diet_filter}'"}),
                    status_code=404,
                    mimetype="application/json",
                    headers={"Access-Control-Allow-Origin": "*"}
                )

        avg_macros = df.groupby('Diet_type')[numeric_cols].mean().round(2)
        top_protein = df.sort_values('Protein(g)', ascending=False).groupby('Diet_type').head(5)
        highest_protein_diet = avg_macros['Protein(g)'].idxmax()
        most_common_cuisine = (
            df.groupby('Diet_type')['Cuisine_type']
            .agg(lambda x: x.value_counts().idxmax())
        )

        execution_time_ms = round((time.time() - start_time) * 1000, 2)

        result = {
            "processed_at": datetime.utcnow().isoformat() + "Z",
            "source": f"azure-blob://{CONTAINER}/{BLOB_NAME}",
            "execution_time_ms": execution_time_ms,
            "avg_macros_per_diet": avg_macros.reset_index().to_dict(orient='records'),
            "top_protein_diet": highest_protein_diet,
            "most_common_cuisine_per_diet": most_common_cuisine.reset_index().to_dict(orient='records'),
            "top_5_protein_recipes": top_protein[['Diet_type', 'Recipe_name', 'Protein(g)']].to_dict(orient='records'),
            "row_count": len(df)
        }

        return func.HttpResponse(
            json.dumps(result, indent=2),
            status_code=200,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

    except Exception as e:
        logging.error(f"Error processing request: {str(e)}")
        return func.HttpResponse(
            json.dumps({"error": str(e)}),
            status_code=500,
            mimetype="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )
