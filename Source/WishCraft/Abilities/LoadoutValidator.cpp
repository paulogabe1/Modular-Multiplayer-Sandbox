#include "Abilities/LoadoutValidator.h"

#include "Abilities/AbilityTokenTable.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"

namespace
{
	FString DescribeCategory(EAbilityCategory Category)
	{
		switch (Category)
		{
		case EAbilityCategory::Offense: return TEXT("Offense");
		case EAbilityCategory::Defense: return TEXT("Defense");
		case EAbilityCategory::Mobility: return TEXT("Mobility");
		default: return TEXT("Unknown");
		}
	}
}

int32 ULoadoutValidator::GetCategoryCap(EAbilityCategory Category)
{
	// The brief's only hard cap: at most one Defense ability per rule set.
	return Category == EAbilityCategory::Defense ? 1 : 99;
}

FLoadoutValidationResult ULoadoutValidator::ValidateTokens(const UAbilityTokenTable* Table, const TArray<FName>& Tokens)
{
	FLoadoutValidationResult Result;

	if (!Table)
	{
		Result.Error = TEXT("No ability list is loaded, so the rules cannot be checked.");
		return Result;
	}

	// 1. Unknown tokens.
	TArray<FName> Unknown;
	for (const FName& Token : Tokens)
	{
		if (!Table->FindToken(Token))
		{
			Unknown.Add(Token);
		}
	}

	if (Unknown.Num() > 0)
	{
		TArray<FString> Names;
		for (const FName& Token : Unknown)
		{
			Names.Add(Token.ToString());
		}
		Result.Error = FString::Printf(TEXT("'%s' is not an ability I know. Try: %s."),
			*FString::Join(Names, TEXT("', '")),
			*Table->GetTokenNameList());
		return Result;
	}

	// 2. Category caps (checked before duplicates so the message names the rule that was broken).
	TMap<EAbilityCategory, int32> CategoryCounts;
	for (const FName& Token : Tokens)
	{
		if (const FAbilityTokenDefinition* Definition = Table->FindToken(Token))
		{
			int32& Count = CategoryCounts.FindOrAdd(Definition->Category);
			++Count;
		}
	}

	for (const TPair<EAbilityCategory, int32>& Pair : CategoryCounts)
	{
		const int32 Cap = GetCategoryCap(Pair.Key);
		if (Pair.Value > Cap)
		{
			Result.Error = FString::Printf(TEXT("Only %d %s ability is allowed, but the rules asked for %d."),
				Cap, *DescribeCategory(Pair.Key), Pair.Value);
			return Result;
		}
	}

	// 3. Duplicates.
	TSet<FName> Seen;
	for (const FName& Token : Tokens)
	{
		if (Seen.Contains(Token))
		{
			Result.Error = FString::Printf(TEXT("'%s' was listed more than once."), *Token.ToString());
			return Result;
		}
		Seen.Add(Token);
	}

	// 4. Build clamped specs.
	for (const FName& Token : Tokens)
	{
		const FAbilityTokenDefinition* Definition = Table->FindToken(Token);
		check(Definition);

		FAbilitySpec Spec;
		Table->BuildDefaultSpec(Token, Spec);

		for (const TPair<FName, FAbilityParamRange>& Range : Definition->ParamRanges)
		{
			float Value = Spec.GetParam(Range.Key, 0.f);
			if (Value == 0.f && !Definition->DefaultParams.Contains(Range.Key))
			{
				Value = Range.Value.Default;
			}
			const float Clamped = Range.Value.ClampValue(Value);
			if (!FMath::IsNearlyEqual(Clamped, Value))
			{
				UE_LOG(LogSandbox, Log, TEXT("Clamped %s.%s from %.2f to %.2f"), *Token.ToString(), *Range.Key.ToString(), Value, Clamped);
				Value = Clamped;
			}

			Spec.SetParam(Range.Key, Value);
		}

		Result.Specs.Add(Spec);
	}

	Result.bValid = true;
	return Result;
}

FLoadoutValidationResult ULoadoutValidator::ValidateIntentJson(const UAbilityTokenTable* Table, const FString& IntentJson)
{
	auto Reject = [](const FString& Error)
	{
		FLoadoutValidationResult Failure;
		Failure.Error = Error;
		return Failure;
	};
	if (!Table || IntentJson.Len() > 65536)
	{
		return Reject(TEXT("The model response cannot be checked against the ability list."));
	}
	TSharedPtr<FJsonObject> Root;
	const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(IntentJson);
	const TArray<TSharedPtr<FJsonValue>>* Entries = nullptr;
	if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid()
		|| Root->Values.Num() != 1 || !Root->TryGetArrayField(TEXT("tokens"), Entries))
	{
		return Reject(TEXT("The model returned an invalid rules response. No rules were changed."));
	}
	if (Entries->IsEmpty() || Entries->Num() > Table->Tokens.Num())
	{
		return Reject(TEXT("The model returned no usable ability selection. No rules were changed."));
	}
	TArray<FName> Names;
	TArray<TSharedPtr<FJsonObject>> Intents;
	for (const TSharedPtr<FJsonValue>& Entry : *Entries)
	{
		if (!Entry.IsValid() || Entry->Type != EJson::Object)
		{
			return Reject(TEXT("The model returned an invalid ability entry."));
		}
		const TSharedPtr<FJsonObject> Object = Entry->AsObject();
		FString Name;
		const TSharedPtr<FJsonObject>* Params = nullptr;
		if (Object->Values.Num() != 2 || !Object->TryGetStringField(TEXT("name"), Name)
			|| Name.IsEmpty() || !Object->TryGetObjectField(TEXT("params"), Params))
		{
			return Reject(TEXT("Each model ability must contain only a name and parameter buckets."));
		}
		Names.Add(FName(*Name));
		Intents.Add(*Params);
	}
	FLoadoutValidationResult Result = ValidateTokens(Table, Names);
	if (!Result.bValid)
	{
		return Result;
	}
	for (int32 Index = 0; Index < Names.Num(); ++Index)
	{
		const FAbilityTokenDefinition* Definition = Table->FindToken(Names[Index]);
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Pair : Intents[Index]->Values)
		{
			const FName ParamName(*Pair.Key);
			const FAbilityParamRange* Range = Definition->ParamRanges.Find(ParamName);
			FString Bucket;
			if (!Range || !Pair.Value.IsValid() || !Pair.Value->TryGetString(Bucket))
			{
				return Reject(FString::Printf(TEXT("Unknown parameter or non-bucket value: %s.%s."),
					*Names[Index].ToString(), *Pair.Key));
			}
			if (!FMath::IsFinite(Range->Min) || !FMath::IsFinite(Range->Max)
				|| !FMath::IsFinite(Range->Default) || Range->Min > Range->Max)
			{
				return Reject(TEXT("The ability table contains an invalid parameter range."));
			}
			float Value = Range->Default;
			if (Bucket == TEXT("much_lower")) { Value = Range->Min; }
			else if (Bucket == TEXT("lower")) { Value = 0.5f * Range->Default; }
			else if (Bucket == TEXT("default")) { Value = Range->Default; }
			else if (Bucket == TEXT("higher")) { Value = 2.f * Range->Default; }
			else if (Bucket == TEXT("max")) { Value = Range->Max; }
			else
			{
				return Reject(FString::Printf(TEXT("Unknown model bucket '%s'. No rules were changed."), *Bucket));
			}
			Value = Range->ClampValue(Value);
			if (!FMath::IsFinite(Value))
			{
				return Reject(TEXT("The ability parameter could not be safely resolved."));
			}
			Result.Specs[Index].SetParam(ParamName, Value);
			UE_LOG(LogSandbox, Log, TEXT("Intent resolved %s.%s: %s -> %.3f"),
				*Names[Index].ToString(), *ParamName.ToString(), *Bucket, Value);
		}
	}
	return Result;
}
