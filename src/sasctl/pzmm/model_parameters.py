# Copyright (c) 2022, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
from distutils.version import StrictVersion
from pathlib import Path
from typing import Union, Tuple, Optional, Any

import pandas as pd
from pandas import DataFrame

from .._services.model_repository import ModelRepository as mr
from ..core import current_session, is_uuid, RestObj


# TODO: Maybe just move _find_file altogether?
def _find_file(model: Union[str, dict, RestObj], file_name: str) -> Tuple[RestObj, str]:
    """
    Retrieves the contents of the first file from a registered model on SAS Model
    Manager that contains the provided file_name as an exact match or substring.

    Parameters
    ----------
    model : str or dict
        The name or id of the model, or a dictionary representation of the model.
    file_name : str
        The name of the desired file or a substring that is contained within the file
        name.

    Returns
    -------
    RestObj, str
        The contents and name of the first file with a name containing file_name.
    """

    file_list = mr.get_model_contents(model)
    for file in file_list:
        if file_name.lower() in file.name.lower():
            correct_file = mr.get(f"models/{model}/contents/{file.id}/content")
            return correct_file, file.name
    raise ValueError(f'No file containing "{file_name}" exists within model files.')


class ModelParameters:
    @staticmethod
    def _update_json(model: str, model_json: dict, kpis: DataFrame) -> dict:
        """
        Updates the contents of the hyperparameter json file

        Parameters
        ----------
        model: str
            The id of the model being updated.
        model_json: dict
            The contents of the current KPI/parameters file within SAS Model Manager.
        kpis: pandas.DataFrame
            The dataframe containing the KPI/parameter values stored within SAS Model
            Manager at runtime.

        Returns
        -------
        dict
            The updated hyperparameter json file to be uploaded to SAS Model Manager.
        """

        model_rows = kpis.loc[kpis["ModelUUID"] == model]
        if not model_rows.empty:
            model_rows = model_rows.drop(columns=["ModelUUID"])
            model_rows.set_index("TimeLabel", inplace=True)
            kpi_json = model_rows.to_json(orient="index")
            parsed_json = json.loads(kpi_json)
            model_json["kpis"] = parsed_json
        return model_json

    @staticmethod
    def generate_hyperparameters(
        model: Any, model_prefix: str, pickle_path: Union[str, Path]
    ) -> None:
        """
        Generates hyperparameters for a given model and creates a JSON file
        representation.

        Currently only supports generation of scikit-learn model hyperparameters.

        This function creates a json file named {model_prefix}Hyperparameters.json.

        Parameters
        ----------
        model : Python object
            Python object representing the model.
        model_prefix : str
            Name used to create model files. (e.g. (model_prefix) +
            "Hyperparameters.json")
        pickle_path : str, Path
            Directory location of model files.
        """

        def sklearn_params():
            """
            Generates hyperparameters for the models generated by scikit-learn.
            """
            hyperparameters = model.get_params()
            model_json = {"hyperparameters": hyperparameters}
            with open(
                Path(pickle_path) / f"{model_prefix}Hyperparameters.json", "w"
            ) as f:
                f.write(json.dumps(model_json, indent=4))

        if all(hasattr(model, attr) for attr in ["_estimator_type", "get_params"]):
            sklearn_params()
        else:
            raise ValueError(
                "This model type is not currently supported for hyperparameter "
                "generation."
            )

    @classmethod
    def update_kpis(
        cls,
        project: Union[str, dict, RestObj],
        server: Optional[str] = "cas-shared-default",
        caslib: Optional[str] = "ModelPerformanceData",
    ) -> None:
        """
        Updates hyperparameter file to include KPIs generated by performance
        definitions, as well as any custom KPIs imported by user to the SAS KPI data
        table.

        Parameters
        ----------
        project : str, dict, or RestObj
            The name or id of the project, or a dictionary representation of the
            project.
        server : str, optional
            Server on which the KPI data table is stored. The default value is
            "cas-shared-default".
        caslib : str, optional
            CAS Library on which the KPI data table is stored. The default value is
            "ModelPerformanceData".
        """
        kpis = cls.get_project_kpis(project, server, caslib)
        models_to_update = kpis["ModelUUID"].unique().tolist()

        for model in models_to_update:
           try:
               current_params, file_name = _find_file(model, "hyperparameters")
           except:
               print(f'No hyperparamter file for current model {kpis.loc[kpis["ModelUUID"]==model, "ModelName"].iloc[0]}. Attempting for next model...')
           else:
               updated_json = cls._update_json(model, current_params, kpis)
               mr.add_model_content(model, json.dumps(updated_json, indent=4), file_name)

    @staticmethod
    def get_hyperparameters(model: Union[str, dict, RestObj]) -> Tuple[dict, str]:
        """
        Retrieves the hyperparameter json file from specified model on SAS Model
        Manager.

        Parameters
        ----------
        model : str, dict, or RestObj
            The name or id of the model, or a dictionary representation of the model.

        Returns
        -------
        dict, str
            Dictionary containing the contents of the hyperparameter file and the file
            name.
        """
        if mr.is_uuid(model):
            id_ = model
        elif isinstance(model, dict) and "id" in model:
            id_ = model["id"]
        else:
            model = mr.get_model(model)
            id_ = model["id"]
        file_contents, file_name = _find_file(id_, "hyperparameters")
        return file_contents, file_name

    @classmethod
    def add_hyperparameters(cls, model: Union[str, dict, RestObj], **kwargs) -> None:
        """
        Adds custom hyperparameters to the hyperparameter file contained within the
        model in SAS Model Manager.

        Parameters
        ----------
        model : str, dict, or RestObj
            The name or id of the model, or a dictionary representation of the model.
        kwargs
            Named variables pairs representing hyperparameters to be added to the
            hyperparameter file.
        """

        if mr.is_uuid(model):
            id_ = model
        elif isinstance(model, dict) and "id" in model:
            id_ = model["id"]
        else:
            model = mr.get_model(model)
            id_ = model["id"]
        hyperparameters, file_name = cls.get_hyperparameters(id_)
        for key, value in kwargs.items():
            hyperparameters["hyperparameters"][key] = value
        mr.add_model_content(
            model,
            json.dumps(hyperparameters, indent=4),
            file_name,
        )

    @staticmethod
    def get_project_kpis(
        project: Union[str, dict, RestObj],
        server: Optional[str] = "cas-shared-default",
        caslib: Optional[str] = "ModelPerformanceData",
        filter_column: Optional[str] = None,
        filter_value: Optional[str] = None,
    ) -> DataFrame:
        """
        Create a call to CAS to return the MM_STD_KPI table (SAS Model Manager
        Standard KPI) generated when custom KPIs are uploaded or when a performance
        definition is executed on SAS Model Manager on SAS Viya 4.

        Filtering options are available as additional arguments. The filtering is based
        on column name and column value. Currently, only exact matches are available
        when filtering by this method.

        Parameters
        ----------
        project : str, dict, RestObj
            The name or id of the project, or a dictionary representation of the
            project.
        server : str, optional
            SAS Viya 4 server where the MM_STD_KPI table exists. The default value is
            "cas-shared-default".
        caslib : str, optional
            SAS Viya 4 caslib where the MM_STD_KPI table exists. The default value is
            "ModelPerformanceData".
        filter_column : str, optional
            Column name from the MM_STD_KPI table to be filtered. The default value is
            None.
        filter_value : str, optional
            Column value filter by. The default value is None

        Returns
        -------
        kpi_table_df : pandas DataFrame
            A pandas DataFrame representing the MM_STD_KPI table. Note that SAS
            missing values are replaced with pandas-valid missing values.
        """
        # Check the pandas version for where the json_normalize function exists
        if pd.__version__ >= StrictVersion("1.0.3"):
            from pandas import json_normalize
        else:
            from pandas.io.json import json_normalize

        # Collect the current session for authentication of API calls
        sess = current_session()

        # Step through options to determine project UUID
        if is_uuid(project):
            project_id = project
        elif isinstance(project, dict) and "id" in project:
            project_id = project["id"]
        else:
            project = mr.get_project(project)
            project_id = project["id"]

        # TODO: include case for large MM_STD_KPI tables
        # Call the casManagement service to collect the column names in the table
        kpi_table_columns = sess.get(
            f"casManagement/servers/{server}/"
            + f"caslibs/{caslib}/tables/"
            + f"{project_id}.MM_STD_KPI/columns?limit=10000"
        )
        if not kpi_table_columns:
            project = mr.get_project(project)
            raise SystemError(
                f"No KPI table exists for project {project.name}."
                + " Please confirm that the performance definition completed"
                + " or custom KPIs have been uploaded successfully."
            )
        # Parse through the json response to create a pandas DataFrame
        cols = json_normalize(kpi_table_columns.json(), "items")
        # Convert the columns to a readable list
        col_names = cols["name"].to_list()

        # Filter rows returned by column and value provided in arguments
        where_statement = ""
        if filter_column and filter_value:
            where_statement = f"&where={filter_column}='{filter_value}'"

        # Call the casRowSets service to return row values
        # Optional where statement is included
        kpi_table_rows = sess.get(
            f"casRowSets/servers/{server}/"
            + f"caslibs/{caslib}/tables/"
            + f"{project_id}.MM_STD_KPI/rows?limit=10000"
            + f"{where_statement}"
        )
        # If no "cells" are found in the json response, return an error
        try:
            kpi_table_df = pd.DataFrame(
                json_normalize(kpi_table_rows.json()["items"])["cells"].to_list(),
                columns=col_names,
            )
        except KeyError:
            if filter_column and filter_value:
                raise SystemError(
                    "No KPIs were found when filtering with {filter_column}='{"
                    "filter_value}'."
                )
            else:
                project_name = mr.get_project(project)["name"]
                raise SystemError(f"No KPIs were found for project {project_name}.")

        # Strip leading spaces from cells of KPI table; convert missing values to None
        kpi_table_df = kpi_table_df.apply(lambda x: x.str.strip()).replace(
            {".": None, "": None}
        )

        return kpi_table_df
