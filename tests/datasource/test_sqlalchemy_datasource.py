import os
from unittest import mock

import pandas as pd
import pytest
from great_expectations.core import ExpectationSuite
from great_expectations.core.batch import Batch
from great_expectations.dataset import SqlAlchemyDataset
from great_expectations.dataset.sqlalchemy_dataset import SqlAlchemyBatchReference
from great_expectations.datasource import SqlAlchemyDatasource
from great_expectations.validator.validator import Validator
from ruamel.yaml import YAML

yaml = YAML()


@pytest.fixture
def test_db_connection_string(tmp_path_factory, test_backends):
    if "sqlite" not in test_backends:
        pytest.skip("skipping fixture because sqlite not selected")
    df1 = pd.DataFrame({"col_1": [1, 2, 3, 4, 5], "col_2": ["a", "b", "c", "d", "e"]})
    df2 = pd.DataFrame({"col_1": [0, 1, 2, 3, 4], "col_2": ["b", "c", "d", "e", "f"]})

    import sqlalchemy as sa

    basepath = str(tmp_path_factory.mktemp("db_context"))
    path = os.path.join(basepath, "test.db")
    engine = sa.create_engine("sqlite:///" + str(path))
    df1.to_sql("table_1", con=engine, index=True)
    df2.to_sql("table_2", con=engine, index=True, schema="main")

    # Return a connection string to this newly-created db
    return "sqlite:///" + str(path)


def test_sqlalchemy_datasource_custom_data_asset(
    data_context, test_db_connection_string
):
    name = "test_sqlalchemy_datasource"
    class_name = "SqlAlchemyDatasource"

    data_asset_type_config = {
        "module_name": "custom_sqlalchemy_dataset",
        "class_name": "CustomSqlAlchemyDataset",
    }
    data_context.add_datasource(
        name,
        class_name=class_name,
        credentials={"connection_string": test_db_connection_string},
        data_asset_type=data_asset_type_config,
        batch_kwargs_generators={
            "default": {"class_name": "TableBatchKwargsGenerator"}
        },
    )

    # We should now see updated configs
    with open(
        os.path.join(data_context.root_directory, "great_expectations.yml"), "r"
    ) as data_context_config_file:
        data_context_file_config = yaml.load(data_context_config_file)

    assert (
        data_context_file_config["datasources"][name]["data_asset_type"]["module_name"]
        == "custom_sqlalchemy_dataset"
    )
    assert (
        data_context_file_config["datasources"][name]["data_asset_type"]["class_name"]
        == "CustomSqlAlchemyDataset"
    )

    # We should be able to get a dataset of the correct type from the datasource.
    data_context.create_expectation_suite("table_1.boo")
    batch = data_context.get_batch(
        data_context.build_batch_kwargs(
            "test_sqlalchemy_datasource", "default", "table_1"
        ),
        "table_1.boo",
    )
    assert type(batch).__name__ == "CustomSqlAlchemyDataset"
    res = batch.expect_column_func_value_to_be("col_1", 1)
    assert res.success is True


def test_standalone_sqlalchemy_datasource(test_db_connection_string, sa):
    datasource = SqlAlchemyDatasource(
        "SqlAlchemy",
        connection_string=test_db_connection_string,
        echo=False,
        batch_kwargs_generators={
            "default": {"class_name": "TableBatchKwargsGenerator"}
        },
    )

    assert set(datasource.get_available_data_asset_names()["default"]["names"]) == {
        ("main.table_1", "table"),
        ("main.table_2", "table"),
    }
    batch_kwargs = datasource.build_batch_kwargs("default", "main.table_1")
    batch = datasource.get_batch(batch_kwargs=batch_kwargs)
    assert isinstance(batch, Batch)
    assert isinstance(batch.data, SqlAlchemyBatchReference)
    dataset = SqlAlchemyDataset(**batch.data.get_init_kwargs())
    assert len(dataset.head(10)) == 5


def test_create_sqlalchemy_datasource(data_context):
    name = "test_sqlalchemy_datasource"
    # type_ = "sqlalchemy"
    class_name = "SqlAlchemyDatasource"

    # Use sqlite so we don't require postgres for this test.
    connection_kwargs = {"credentials": {"drivername": "sqlite"}}

    # It should be possible to create a sqlalchemy source using these params without
    # saving substitution variables
    data_context.add_datasource(name, class_name=class_name, **connection_kwargs)
    data_context_config = data_context.get_config()
    assert name in data_context_config["datasources"]
    assert data_context_config["datasources"][name]["class_name"] == class_name

    # We should be able to get it in this session even without saving the config
    source = data_context.get_datasource(name)
    assert isinstance(source, SqlAlchemyDatasource)

    var_name = "test_sqlalchemy_datasource"

    data_context.save_config_variable(var_name, connection_kwargs["credentials"])

    # But we should be able to add a source using a substitution variable
    name = "second_source"
    data_context.add_datasource(
        name, class_name=class_name, credentials="${" + var_name + "}"
    )

    data_context_config = data_context.get_config()
    assert name in data_context_config["datasources"]
    assert data_context_config["datasources"][name]["class_name"] == class_name
    assert (
        data_context_config["datasources"][name]["credentials"] == "${" + var_name + "}"
    )

    source = data_context.get_datasource(name)
    assert isinstance(source, SqlAlchemyDatasource)

    # Finally, we should be able to confirm that the folder structure is as expected
    with open(
        os.path.join(data_context.root_directory, "uncommitted/config_variables.yml"),
        "r",
    ) as credentials_file:
        substitution_variables = yaml.load(credentials_file)

    assert substitution_variables == {
        var_name: dict(**connection_kwargs["credentials"])
    }


def test_sqlalchemy_source_templating(sqlitedb_engine):
    datasource = SqlAlchemyDatasource(
        engine=sqlitedb_engine,
        batch_kwargs_generators={"foo": {"class_name": "QueryBatchKwargsGenerator"}},
    )
    generator = datasource.get_batch_kwargs_generator("foo")
    generator.add_query("test", "select 'cat' as ${col_name};")
    batch = datasource.get_batch(
        generator.build_batch_kwargs(
            "test", query_parameters={"col_name": "animal_name"}
        )
    )
    dataset = Validator(
        batch,
        expectation_suite=ExpectationSuite("test"),
        expectation_engine=SqlAlchemyDataset,
    ).get_dataset()
    res = dataset.expect_column_to_exist("animal_name")
    assert res.success is True
    res = dataset.expect_column_values_to_be_in_set("animal_name", ["cat"])
    assert res.success is True


def test_sqlalchemy_source_limit(sqlitedb_engine):
    df1 = pd.DataFrame({"col_1": [1, 2, 3, 4, 5], "col_2": ["a", "b", "c", "d", "e"]})
    df2 = pd.DataFrame({"col_1": [0, 1, 2, 3, 4], "col_2": ["b", "c", "d", "e", "f"]})
    df1.to_sql("table_1", con=sqlitedb_engine, index=True)
    df2.to_sql("table_2", con=sqlitedb_engine, index=True, schema="main")
    datasource = SqlAlchemyDatasource("SqlAlchemy", engine=sqlitedb_engine)
    limited_batch = datasource.get_batch({"table": "table_1", "limit": 1, "offset": 2})
    assert isinstance(limited_batch, Batch)
    limited_dataset = Validator(
        limited_batch,
        expectation_suite=ExpectationSuite("test"),
        expectation_engine=SqlAlchemyDataset,
    ).get_dataset()
    assert limited_dataset._table.name.startswith(
        "ge_tmp_"
    )  # we have generated a temporary table
    assert len(limited_dataset.head(10)) == 1  # and it is only one row long
    assert limited_dataset.head(10)["col_1"][0] == 3  # offset should have been applied


def test_sqlalchemy_datasource_query_and_table_handling(sqlitedb_engine):
    # MANUALLY SET DIALECT NAME FOR TEST
    datasource = SqlAlchemyDatasource("SqlAlchemy", engine=sqlitedb_engine)
    with mock.patch(
        "great_expectations.dataset.sqlalchemy_dataset.SqlAlchemyBatchReference.__init__",
        return_value=None,
    ) as mock_batch:
        datasource.get_batch({"query": "select * from foo;"})
    mock_batch.assert_called_once_with(
        engine=sqlitedb_engine, schema=None, query="select * from foo;", table_name=None
    )

    # Normally, we do not allow both query and table_name
    with mock.patch(
        "great_expectations.dataset.sqlalchemy_dataset.SqlAlchemyBatchReference.__init__",
        return_value=None,
    ) as mock_batch:
        datasource.get_batch({"query": "select * from foo;", "table_name": "bar"})
    mock_batch.assert_called_once_with(
        engine=sqlitedb_engine, schema=None, query="select * from foo;", table_name=None
    )

    # Snowflake should require query *and* snowflake_transient_table
    sqlitedb_engine.dialect.name = "snowflake"
    with mock.patch(
        "great_expectations.dataset.sqlalchemy_dataset.SqlAlchemyBatchReference.__init__",
        return_value=None,
    ) as mock_batch:
        datasource.get_batch(
            {"query": "select * from foo;", "snowflake_transient_table": "bar"}
        )
    mock_batch.assert_called_once_with(
        engine=sqlitedb_engine,
        schema=None,
        query="select * from foo;",
        table_name="bar",
    )


def test_sqlalchemy_datasource_processes_dataset_options(test_db_connection_string):
    datasource = SqlAlchemyDatasource(
        "SqlAlchemy", credentials={"url": test_db_connection_string}
    )
    batch_kwargs = datasource.process_batch_parameters(
        dataset_options={"caching": False}
    )
    batch_kwargs["query"] = "select * from table_1;"
    batch = datasource.get_batch(batch_kwargs)
    validator = Validator(batch, ExpectationSuite(expectation_suite_name="foo"))
    dataset = validator.get_dataset()
    assert dataset.caching is False

    batch_kwargs = datasource.process_batch_parameters(
        dataset_options={"caching": True}
    )
    batch_kwargs["query"] = "select * from table_1;"
    batch = datasource.get_batch(batch_kwargs)
    validator = Validator(batch, ExpectationSuite(expectation_suite_name="foo"))
    dataset = validator.get_dataset()
    assert dataset.caching is True

    batch_kwargs = {
        "query": "select * from table_1;",
        "dataset_options": {"caching": False},
    }
    batch = datasource.get_batch(batch_kwargs)
    validator = Validator(batch, ExpectationSuite(expectation_suite_name="foo"))
    dataset = validator.get_dataset()
    assert dataset.caching is False
